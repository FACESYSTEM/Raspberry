// F-SERVICE 個室カメラ（Seeed Studio XIAO ESP32S3 Sense ＋ 人感センサー AM312）
//
// 人感センサーが反応したら数枚撮って microSD に書き、終わったら店の Pi 5（受け口 fservice-hub）へ送る。
// 顔・グループの判定はすべてサーバが後で行う。この端末は「撮って送る」だけ。
// 仕様: docs/room-camera.md
//
// 設定は microSD の /fscam.txt（Mac で書き換えられる）:
//   ssid=店のWi-Fi名
//   password=パスワード
//   hub=fs-pi-test.local        Pi 5 の名前か IP アドレス（:8650 は省略可）
//   cam=room1                   部屋の名前（Pi 5 の add-room.sh と同じ）
//   key=合言葉                   Pi 5 の /etc/fservice-pi/hub.key の中身
// 省略できるもの（かっこ内が既定）:
//   size=sxga（vga / svga / xga / hd / sxga / uxga）  fps=3  burst=10（秒）  max=60（秒）
//   quality=12（小さいほど高画質）  warmup_ms=600  still_min=5  flip=0  mirror=0
//   test_min=0（0 より大きいと、人感センサーなしでこの分おきに撮る。慣らし運転用）
//
// 配線: 人感センサー VCC → 3V3、GND → GND、OUT → D0
// 書き込み: Arduino IDE、ボード「XIAO_ESP32S3」、PSRAM「OPI PSRAM」（docs/room-camera-flash.md）

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>
#include <SPI.h>
#include <SD.h>
#include <Preferences.h>
#include <sys/time.h>
#include <time.h>
#include "esp_camera.h"
#include "esp_task_wdt.h"
#include "esp_system.h"

#if __has_include("ESP32_OV5640_AF.h")
#include "ESP32_OV5640_AF.h"
#define HAVE_OV5640_AF 1
OV5640 ov5640 = OV5640();
#endif

#define FW_VERSION "fscam-0.1.0"
#define PIR_PIN D0
#define SD_CS 21
#define HUB_PORT_DEFAULT 8650
#define T_MIN_MS 1600000000000ULL  // これより前の時刻は「合っていない」

// XIAO ESP32S3 Sense のカメラの配線（Seeed の camera_pins.h と同じ）
#define PWDN_GPIO_NUM -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 10
#define SIOD_GPIO_NUM 40
#define SIOC_GPIO_NUM 39
#define Y9_GPIO_NUM 48
#define Y8_GPIO_NUM 11
#define Y7_GPIO_NUM 12
#define Y6_GPIO_NUM 14
#define Y5_GPIO_NUM 16
#define Y4_GPIO_NUM 18
#define Y3_GPIO_NUM 17
#define Y2_GPIO_NUM 15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM 47
#define PCLK_GPIO_NUM 13

// ---- 設定 ----
struct Cfg {
  String ssid, password, hub, cam, key;
  String size = "sxga";
  int fps = 3, burst = 10, maxSec = 60, quality = 12, warmupMs = 600, stillMin = 5, testMin = 0;
  bool flip = false, mirror = false;
} cfg;

// ---- 状態（5 分ごとに Pi 5 へ送る）----
Preferences prefs;
uint32_t boots = 0, quickBoots = 0;
String resetWhy;
uint32_t camErr = 0, sdErr = 0, wifiErr = 0, sendErr = 0, dropped = 0, pirStuck = 0, shots = 0;
bool sdOk = false, camOn = false, safeMode = false;
String sensorName = "?";
uint32_t pendingN = 0;         // microSD に残っている撮影の数（毎回数えると microSD を読み続けるので覚えておく）
uint32_t lastSendFail = 0;
bool captureAllowed = false;
uint32_t stateAt = 0;          // 最後に「撮ってよいか」を受け取った millis（0 = まだ）
uint32_t lastStill = 0, lastStateAsk = 0, lastWifiOk = 0, lastTest = 0;
IPAddress hubIp;
uint16_t hubPort = HUB_PORT_DEFAULT;
bool hubResolved = false;
int camFails = 0;

// ---- 小物 ----
void logf(const char *fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  Serial.printf("[%8lu] %s\n", (unsigned long)(millis() / 1000), buf);
}

void feed() { esp_task_wdt_reset(); }

uint64_t nowMs() {
  struct timeval tv;
  gettimeofday(&tv, nullptr);
  return (uint64_t)tv.tv_sec * 1000ULL + tv.tv_usec / 1000;
}

bool timeOk() { return nowMs() > T_MIN_MS; }

void setTimeMs(uint64_t t) {
  if (t < T_MIN_MS) return;
  int64_t diff = (int64_t)(t - nowMs());
  if (diff > 1000 || diff < -1000 || !timeOk()) {
    struct timeval tv = {(time_t)(t / 1000), (suseconds_t)((t % 1000) * 1000)};
    settimeofday(&tv, nullptr);
  }
}

const char *resetName(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_SW: return "restart";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    default: return "other";
  }
}

void restartNow(const char *why) {
  logf("再起動します: %s", why);
  prefs.putString("why", why);
  delay(200);
  ESP.restart();
}

// ---- 設定ファイル ----
bool loadConfig() {
  File f = SD.open("/fscam.txt", FILE_READ);
  if (!f) return false;
  while (f.available()) {
    String line = f.readStringUntil('\n');
    line.trim();
    if (line.length() == 0 || line[0] == '#') continue;
    int eq = line.indexOf('=');
    if (eq < 0) continue;
    String k = line.substring(0, eq), v = line.substring(eq + 1);
    k.trim();
    k.toLowerCase();
    if (k != "password" && k != "ssid" && k != "key") {
      int c = v.indexOf(" #");  // 値の後ろの説明は落とす（パスワード等は # を含むことがあるので除く）
      if (c >= 0) v = v.substring(0, c);
    }
    v.trim();
    if (k == "ssid") cfg.ssid = v;
    else if (k == "password") cfg.password = v;
    else if (k == "hub") cfg.hub = v;
    else if (k == "cam") cfg.cam = v;
    else if (k == "key") cfg.key = v;
    else if (k == "size") cfg.size = v;
    else if (k == "fps") cfg.fps = constrain(v.toInt(), 1, 10);
    else if (k == "burst") cfg.burst = constrain(v.toInt(), 2, 60);
    else if (k == "max") cfg.maxSec = constrain(v.toInt(), 5, 120);
    else if (k == "quality") cfg.quality = constrain(v.toInt(), 6, 40);
    else if (k == "warmup_ms") cfg.warmupMs = constrain(v.toInt(), 0, 3000);
    else if (k == "still_min") cfg.stillMin = constrain(v.toInt(), 1, 60);
    else if (k == "test_min") cfg.testMin = constrain(v.toInt(), 0, 600);
    else if (k == "flip") cfg.flip = v.toInt() != 0;
    else if (k == "mirror") cfg.mirror = v.toInt() != 0;
  }
  f.close();
  int colon = cfg.hub.lastIndexOf(':');
  if (colon > 0) {
    hubPort = cfg.hub.substring(colon + 1).toInt();
    cfg.hub = cfg.hub.substring(0, colon);
  }
  return cfg.ssid.length() && cfg.hub.length() && cfg.cam.length();
}

framesize_t frameSize() {
  if (cfg.size == "vga") return FRAMESIZE_VGA;
  if (cfg.size == "svga") return FRAMESIZE_SVGA;
  if (cfg.size == "xga") return FRAMESIZE_XGA;
  if (cfg.size == "hd") return FRAMESIZE_HD;
  if (cfg.size == "uxga") return FRAMESIZE_UXGA;
  return FRAMESIZE_SXGA;
}

// ---- カメラ ----
bool cameraStart() {
  if (camOn) return true;
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = frameSize();
  c.jpeg_quality = cfg.quality;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    camErr++;
    logf("カメラを開けない: 0x%x", err);
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    switch (s->id.PID) {
      case OV2640_PID: sensorName = "OV2640"; break;
      case OV3660_PID: sensorName = "OV3660"; break;
      case OV5640_PID: sensorName = "OV5640"; break;
      default: sensorName = String(s->id.PID, HEX);
    }
    s->set_vflip(s, cfg.flip ? 1 : 0);
    s->set_hmirror(s, cfg.mirror ? 1 : 0);
#ifdef HAVE_OV5640_AF
    if (s->id.PID == OV5640_PID) {
      ov5640.start(s);
      if (ov5640.focusInit() == 0) ov5640.autoFocusMode();
    }
#endif
  }
  camOn = true;
  // 明るさが落ち着くまでの数枚は捨てる
  uint32_t until = millis() + cfg.warmupMs;
  while ((int32_t)(millis() - until) < 0) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
    feed();
  }
  return true;
}

void cameraStop() {
  if (!camOn) return;
  esp_camera_deinit();
  camOn = false;
}

camera_fb_t *grab() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb || fb->format != PIXFORMAT_JPEG || fb->len < 2) {
    if (fb) esp_camera_fb_return(fb);
    camErr++;
    camFails++;
    return nullptr;
  }
  camFails = 0;
  return fb;
}

// ---- microSD ----
uint32_t pendingClips() {
  if (!sdOk) return 0;
  uint32_t n = 0;
  File dir = SD.open("/clips");
  if (!dir) return 0;
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    if (String(f.name()).endsWith(".fsc")) n++;
    f.close();
  }
  dir.close();
  return n;
}

String oldestClip() {
  String best;
  File dir = SD.open("/clips");
  if (!dir) return best;
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    String name = f.name();
    f.close();
    if (!name.endsWith(".fsc")) continue;
    if (best.length() == 0 || name < best) best = name;  // 名前は撮り始めの時刻（桁数は同じ）
  }
  dir.close();
  return best.length() ? "/clips/" + best : best;
}

void keepSdSpace() {
  // 空きが 200MB を切ったら、古い撮影から消す
  while (sdOk && SD.totalBytes() - SD.usedBytes() < 200ULL * 1024 * 1024) {
    String p = oldestClip();
    if (p.length() == 0) break;
    SD.remove(p);
    dropped++;
    if (pendingN) pendingN--;
    logf("microSD がいっぱい。古い撮影を消した: %s", p.c_str());
  }
}

void recoverTmp() {
  // 書き込み中の停電で残った .tmp は、読める分だけ送る（受け口が壊れた末尾を見分ける）
  File dir = SD.open("/clips");
  if (!dir) return;
  String names[16];
  int n = 0;
  for (File f = dir.openNextFile(); f && n < 16; f = dir.openNextFile()) {
    String name = f.name();
    size_t size = f.size();
    f.close();
    if (name.endsWith(".tmp")) {
      if (size == 0) SD.remove("/clips/" + name);
      else names[n++] = name;
    }
  }
  dir.close();
  for (int i = 0; i < n; i++) {
    String from = "/clips/" + names[i];
    String to = from.substring(0, from.length() - 4) + ".fsc";
    SD.rename(from, to);
  }
}

// ---- Pi 5（受け口）----
bool resolveHub() {
  if (hubIp.fromString(cfg.hub)) {
    hubResolved = true;
    return true;
  }
  String name = cfg.hub;
  if (name.endsWith(".local")) name = name.substring(0, name.length() - 6);
  IPAddress ip = MDNS.queryHost(name, 3000);
  if (ip == IPAddress(0, 0, 0, 0)) {
    hubResolved = false;
    return false;
  }
  hubIp = ip;
  hubResolved = true;
  return true;
}

String hubUrl(const char *path) {
  return "http://" + hubIp.toString() + ":" + String(hubPort) + path + "?cam=" + cfg.cam;
}

uint64_t jsonNum(const String &body, const char *key) {
  String k = String("\"") + key + "\":";
  int i = body.indexOf(k);
  if (i < 0) return 0;
  i += k.length();
  while (i < (int)body.length() && body[i] == ' ') i++;
  return strtoull(body.c_str() + i, nullptr, 10);
}

bool jsonTrue(const String &body, const char *key) {
  String k = String("\"") + key + "\":";
  int i = body.indexOf(k);
  if (i < 0) return false;
  i += k.length();
  while (i < (int)body.length() && body[i] == ' ') i++;
  return body.startsWith("true", i);
}

// 返事から時刻と「撮ってよいか」を拾う
void takeReply(const String &body) {
  setTimeMs(jsonNum(body, "t"));
  if (body.indexOf("\"capture\"") >= 0) {
    captureAllowed = jsonTrue(body, "capture");
    stateAt = millis();
  }
}

int hubRequest(const char *method, const char *path, Stream *stream, size_t size, const String &status,
               String &reply) {
  if (WiFi.status() != WL_CONNECTED) return -1;
  if (!hubResolved && !resolveHub()) return -2;
  HTTPClient http;
  http.setTimeout(20000);
  http.begin(hubUrl(path));
  if (cfg.key.length()) http.addHeader("X-Key", cfg.key);
  if (status.length()) http.addHeader("X-Status", status);
  int code;
  if (stream) {
    http.addHeader("Content-Type", "application/octet-stream");
    code = http.sendRequest(method, stream, size);
  } else {
    code = http.sendRequest(method, (uint8_t *)nullptr, 0);
  }
  if (code > 0) reply = http.getString();
  http.end();
  if (code <= 0) hubResolved = false;  // つながらなければ次は名前から探し直す
  return code;
}

void askState() {
  String reply;
  int code = hubRequest("GET", "/v1/state", nullptr, 0, "", reply);
  if (code == 200) takeReply(reply);
}

String statusJson() {
  char buf[400];
  snprintf(buf, sizeof(buf),
           "{\"fw\":\"%s\",\"sensor\":\"%s\",\"rssi\":%d,\"temp\":%.1f,\"boots\":%lu,\"reset\":\"%s\","
           "\"cam_err\":%lu,\"sd_err\":%lu,\"wifi_err\":%lu,\"send_err\":%lu,\"dropped\":%lu,"
           "\"pir_stuck\":%lu,\"shots\":%lu,\"pending\":%lu,\"heap\":%lu,\"psram\":%lu,\"up\":%lu,"
           "\"sd\":%s,\"safe\":%s,\"time_ok\":%s}",
           FW_VERSION, sensorName.c_str(), (int)WiFi.RSSI(), temperatureRead(), (unsigned long)boots,
           resetWhy.c_str(), (unsigned long)camErr, (unsigned long)sdErr, (unsigned long)wifiErr,
           (unsigned long)sendErr, (unsigned long)dropped, (unsigned long)pirStuck, (unsigned long)shots,
           (unsigned long)pendingN, (unsigned long)ESP.getFreeHeap(), (unsigned long)ESP.getFreePsram(),
           (unsigned long)(millis() / 1000), sdOk ? "true" : "false", safeMode ? "true" : "false",
           timeOk() ? "true" : "false");
  return String(buf);
}

// 5 分ごとの静止画と状態（生存確認・画角確認用）
void sendStill() {
  lastStill = millis();
  String status = statusJson();
  String reply;
  int code;
  if (cameraStart()) {
    camera_fb_t *fb = grab();
    cameraStop();
    if (fb) {
      // 静止画は JPEG をそのまま送る（バッファを Stream にするため一度コピーする）
      uint8_t *copy = (uint8_t *)ps_malloc(fb->len);
      size_t len = fb->len;
      if (copy) memcpy(copy, fb->buf, len);
      esp_camera_fb_return(fb);
      if (copy) {
        HTTPClient http;
        if (WiFi.status() == WL_CONNECTED && (hubResolved || resolveHub())) {
          http.setTimeout(20000);
          http.begin(hubUrl("/v1/still"));
          if (cfg.key.length()) http.addHeader("X-Key", cfg.key);
          http.addHeader("X-Status", status);
          http.addHeader("Content-Type", "image/jpeg");
          code = http.sendRequest("POST", copy, len);
          if (code > 0) reply = http.getString();
          http.end();
          if (code == 200) takeReply(reply);
          else sendErr++;
        }
        free(copy);
        return;
      }
    }
  }
  // カメラが使えなくても状態だけは送る（止まっていると Pi 5 で分かるように）
  code = hubRequest("POST", "/v1/still", nullptr, 0, status, reply);
  if (code == 200) takeReply(reply);
}

// microSD の撮影を 1 つ送る。送れたら消す
bool sendOneClip() {
  if (!sdOk || WiFi.status() != WL_CONNECTED) return false;
  String path = oldestClip();
  if (path.length() == 0) {
    pendingN = 0;
    return false;
  }
  File f = SD.open(path, FILE_READ);
  if (!f) {
    sdErr++;
    return false;
  }
  size_t size = f.size();
  String reply;
  int code = hubRequest("POST", "/v1/clip", &f, size, "", reply);
  f.close();
  if (code == 200 && jsonTrue(reply, "ok")) {
    takeReply(reply);
    SD.remove(path);
    if (pendingN) pendingN--;
    logf("送った: %s（%u バイト）", path.c_str(), (unsigned)size);
    return true;
  }
  if (code == 400) {  // 壊れた撮影（停電など）。読めた分は受け口が受け取っている
    SD.remove(path);
    if (pendingN) pendingN--;
    logf("壊れた撮影を消した: %s", path.c_str());
    return true;
  }
  sendErr++;
  lastSendFail = millis();
  logf("送れない（%d）: %s。後で送り直す", code, path.c_str());
  return false;
}

// ---- 撮影 ----
// 撮影の形式: "FSF1" + 時刻(8) + 長さ(4) + JPEG（docs/room-camera.md）
bool writeFrame(File &f, uint64_t t, const uint8_t *buf, uint32_t len) {
  uint8_t head[16];
  memcpy(head, "FSF1", 4);
  memcpy(head + 4, &t, 8);  // ESP32 はリトルエンディアン
  memcpy(head + 12, &len, 4);
  return f.write(head, 16) == 16 && f.write(buf, len) == len;
}

void recordBurst(const char *why) {
  if (!timeOk()) {
    logf("時刻が合っていないので撮らない");
    return;
  }
  if (!cameraStart()) {
    if (camFails >= 3 || camErr >= 3) restartNow("camera");
    return;
  }
  uint64_t t0 = nowMs();
  String tmp = "/clips/" + String((unsigned long long)t0) + ".tmp";
  File f;
  bool toSd = sdOk;
  if (toSd) {
    f = SD.open(tmp, FILE_WRITE);
    if (!f) {
      sdErr++;
      toSd = false;
    }
  }
  // microSD が使えないときは、メモリに貯めてそのまま送る（上限 4MB）
  uint8_t *mem = nullptr;
  size_t memLen = 0, memCap = 0;
  if (!toSd) {
    memCap = 4 * 1024 * 1024;
    mem = (uint8_t *)ps_malloc(memCap);
  }
  logf("撮影開始（%s）", why);
  uint32_t start = millis(), lastSeen = millis(), next = millis();
  uint32_t interval = 1000 / cfg.fps;
  int n = 0;
  while (true) {
    feed();
    uint32_t now = millis();
    if (digitalRead(PIR_PIN) == HIGH) lastSeen = now;
    if (now - lastSeen > (uint32_t)cfg.burst * 1000) break;      // 反応がなくなって burst 秒
    if (now - start > (uint32_t)cfg.maxSec * 1000) break;         // 1 回の上限
    if (temperatureRead() > 80) {                                  // 熱すぎる
      logf("本体が熱いので撮影を止める");
      break;
    }
    if ((int32_t)(now - next) < 0) {
      delay(5);
      continue;
    }
    next = now + interval;
    camera_fb_t *fb = grab();
    if (!fb) {
      if (camFails >= 3) break;
      continue;
    }
    uint64_t t = nowMs();
    bool ok;
    if (toSd) {
      ok = writeFrame(f, t, fb->buf, fb->len);
      if (!ok) sdErr++;
    } else if (mem && memLen + 16 + fb->len <= memCap) {
      memcpy(mem + memLen, "FSF1", 4);
      memcpy(mem + memLen + 4, &t, 8);
      uint32_t len = fb->len;
      memcpy(mem + memLen + 12, &len, 4);
      memcpy(mem + memLen + 16, fb->buf, len);
      memLen += 16 + len;
      ok = true;
    } else {
      ok = false;
      dropped++;
    }
    esp_camera_fb_return(fb);
    if (ok) {
      n++;
      shots++;
    }
  }
  cameraStop();
  logf("撮影終了: %d 枚（%lu 秒）", n, (unsigned long)((millis() - start) / 1000));
  if (toSd) {
    f.close();
    if (n > 0) {
      SD.rename(tmp, tmp.substring(0, tmp.length() - 4) + ".fsc");
      pendingN++;
    } else {
      SD.remove(tmp);
    }
    keepSdSpace();
  } else if (mem) {
    if (n > 0 && WiFi.status() == WL_CONNECTED && (hubResolved || resolveHub())) {
      HTTPClient http;
      http.setTimeout(20000);
      http.begin(hubUrl("/v1/clip"));
      if (cfg.key.length()) http.addHeader("X-Key", cfg.key);
      http.addHeader("Content-Type", "application/octet-stream");
      int code = http.sendRequest("POST", mem, memLen);
      String reply = code > 0 ? http.getString() : "";
      http.end();
      if (code == 200) takeReply(reply);
      else {
        sendErr++;
        dropped += n;
      }
    } else if (n > 0) {
      dropped += n;
    }
    free(mem);
  }
  if (camFails >= 3) restartNow("camera");
}

// ---- 起動 ----
void setup() {
  Serial.begin(115200);
  delay(300);
  logf("%s 起動", FW_VERSION);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  // 固まったら 120 秒で再起動（送信に時間がかかることがあるので長め）
  esp_task_wdt_config_t wdt = {.timeout_ms = 120000, .idle_core_mask = 0, .trigger_panic = true};
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
#else
  esp_task_wdt_init(120, true);
#endif
  esp_task_wdt_add(NULL);

  prefs.begin("fscam", false);
  boots = prefs.getUInt("boots", 0) + 1;
  prefs.putUInt("boots", boots);
  quickBoots = prefs.getUInt("quick", 0) + 1;
  prefs.putUInt("quick", quickBoots);
  resetWhy = resetName(esp_reset_reason());
  String why = prefs.getString("why", "");
  if (why.length() && resetWhy == "restart") resetWhy = "restart:" + why;
  prefs.putString("why", "");
  // 短い間に何度も再起動している → 撮影を止めて生存確認だけ続ける
  safeMode = quickBoots >= 5;
  logf("起動 %lu 回目（理由 %s）%s", (unsigned long)boots, resetWhy.c_str(), safeMode ? " 安全モード" : "");

  pinMode(PIR_PIN, INPUT);
  setenv("TZ", "JST-9", 1);
  tzset();

  sdOk = SD.begin(SD_CS);
  if (!sdOk) {
    sdErr++;
    logf("microSD が使えない。設定が読めないので 30 秒後に再起動する");
    delay(30000);
    restartNow("no-sd");
  }
  if (!loadConfig()) {
    logf("/fscam.txt が無いか、ssid・hub・cam が足りない。60 秒後に再起動する");
    delay(60000);
    restartNow("no-config");
  }
  SD.mkdir("/clips");
  recoverTmp();
  pendingN = pendingClips();
  logf("部屋 %s・Pi %s:%u・大きさ %s・毎秒 %d 枚", cfg.cam.c_str(), cfg.hub.c_str(), hubPort, cfg.size.c_str(),
       cfg.fps);

  WiFi.setHostname(("fscam-" + cfg.cam).c_str());
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(cfg.ssid.c_str(), cfg.password.c_str());
  MDNS.begin(("fscam-" + cfg.cam).c_str());
  configTime(0, 0, "ntp.nict.jp", "pool.ntp.org");  // Pi 5 から時刻を取れないときの予備
  lastWifiOk = millis();
  lastStill = millis() - (uint32_t)cfg.stillMin * 60000 + 15000;  // 起動 15 秒後に 1 回目
}

// ---- 毎回 ----
void loop() {
  feed();
  uint32_t now = millis();

  // Wi-Fi
  static bool wasUp = false;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up) {
    if (!wasUp) logf("Wi-Fi につながった（%s・電波 %d）", WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
    lastWifiOk = now;
  } else if (wasUp) {
    wifiErr++;
    logf("Wi-Fi が切れた");
  }
  wasUp = up;
  if (!up && now - lastWifiOk > 10UL * 60000) restartNow("wifi");

  // 起動してしばらく問題なければ「短い間の再起動」を数え直す
  static bool settled = false;
  if (!settled && now > 10UL * 60000) {
    settled = true;
    prefs.putUInt("quick", 0);
  }

  // 撮ってよいかを 1 分ごとに聞く
  if (up && (lastStateAsk == 0 || now - lastStateAsk > 60000)) {
    lastStateAsk = now;
    askState();
  }
  // 最後に聞けたのが 2 時間以上前なら、撮らない（店の設定が分からないまま撮り続けない）
  bool allowed = captureAllowed && stateAt != 0 && now - stateAt < 2UL * 3600 * 1000 && !safeMode;

  // 人感センサー
  static uint32_t pirHighSince = 0;
  static bool pirIgnored = false;
  bool pir = digitalRead(PIR_PIN) == HIGH;
  if (pir) {
    if (pirHighSince == 0) pirHighSince = now;
    if (!pirIgnored && now - pirHighSince > 5UL * 60000) {  // 5 分反応しっぱなし → 壊れているとみて無視
      pirIgnored = true;
      pirStuck++;
      logf("人感センサーが反応しっぱなし。下がるまで無視する");
    }
  } else {
    pirHighSince = 0;
    pirIgnored = false;
  }

  if (allowed && pir && !pirIgnored) {
    recordBurst("人感センサー");
  } else if (allowed && cfg.testMin > 0 && now - lastTest > (uint32_t)cfg.testMin * 60000) {
    lastTest = now;
    recordBurst("慣らし運転");
  } else if (up && pendingN > 0 && (lastSendFail == 0 || now - lastSendFail > 30000)) {
    sendOneClip();  // 送れなかったら 30 秒あけて送り直す
  } else if (now - lastStill > (uint32_t)cfg.stillMin * 60000) {
    sendStill();
  }

  // メモリが減ってきた・毎日 4 時台（営業時間外）に 1 回、撮影していないときに再起動
  if (ESP.getFreeHeap() < 40000) restartNow("low-heap");
  static int lastRebootDay = -1;
  time_t tt = time(nullptr);
  struct tm lt;
  localtime_r(&tt, &lt);
  if (timeOk() && lt.tm_hour == 4 && millis() > 3600UL * 1000 && lastRebootDay != lt.tm_yday) {
    lastRebootDay = lt.tm_yday;
    restartNow("daily");
  }
  delay(50);
}
