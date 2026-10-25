#include "ui.h"
#include "version.h"

#include <citro2d.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#define TOP_W 400
#define BOTTOM_W 320
#define H 240
#define MAX_REPORT 8
#define MAX_LOG 7

// colours: vcinject's purple on a dark night blue
#define C(r, g, b) C2D_Color32(r, g, b, 255)
static u32 BG_TOP, BG_BOTTOM, CARD, CARD_EDGE, PURPLE, PURPLE_LIGHT, TEXT, MUTED, GREEN, ORANGE,
    RED, BLUE;

typedef struct {
    char level;  // 'o'k, 'u'pdate, 'w'arn, 'i'nfo
    char title[40];
    char detail[64];
} Line;

static struct {
    C3D_RenderTarget *top, *bottom;
    C2D_TextBuf text;
    char address[24];
    bool network_ok;
    char peer[48];
    u64 contact_ms;
    char activity[80];
    float progress;
    Line report[MAX_REPORT];
    int report_count;
    bool report_from_vcinject;
    char report_from[40];
    char log[MAX_LOG][64];
    char log_time[MAX_LOG][6];
    int log_count;
    u32 frame;
} ui;

void ui_init(void) {
    BG_TOP = C(24, 21, 43);
    BG_BOTTOM = C(19, 17, 34);
    CARD = C(38, 33, 66);
    CARD_EDGE = C(56, 49, 94);
    PURPLE = C(118, 92, 206);
    PURPLE_LIGHT = C(171, 152, 240);
    TEXT = C(240, 238, 250);
    MUTED = C(150, 144, 184);
    GREEN = C(76, 208, 138);
    ORANGE = C(245, 166, 35);
    RED = C(229, 83, 75);
    BLUE = C(90, 169, 245);

    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    C2D_Init(C2D_DEFAULT_MAX_OBJECTS);
    C2D_Prepare();
    ui.top = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    ui.bottom = C2D_CreateScreenTarget(GFX_BOTTOM, GFX_LEFT);
    ui.text = C2D_TextBufNew(8192);
    ui.progress = -1;
    ptmuInit();
}

void ui_exit(void) {
    ptmuExit();
    C2D_TextBufDelete(ui.text);
    C2D_Fini();
    C3D_Fini();
}

// --- state ---------------------------------------------------------------------

void ui_set_address(u32 ip, bool network_ok) {
    ui.network_ok = network_ok;
    snprintf(ui.address, sizeof ui.address, "%lu.%lu.%lu.%lu", ip & 0xFF, (ip >> 8) & 0xFF,
             (ip >> 16) & 0xFF, ip >> 24);
}

void ui_contact(const char *peer) {
    snprintf(ui.peer, sizeof ui.peer, "%s", peer);
    ui.contact_ms = osGetTime();
}

void ui_set_activity(const char *text, float progress) {
    snprintf(ui.activity, sizeof ui.activity, "%s", text ? text : "");
    ui.progress = progress;
}

void ui_log(const char *fmt, ...) {
    if (ui.log_count == MAX_LOG) {
        memmove(ui.log, ui.log[1], sizeof ui.log[0] * (MAX_LOG - 1));
        memmove(ui.log_time, ui.log_time[1], sizeof ui.log_time[0] * (MAX_LOG - 1));
        ui.log_count--;
    }
    va_list args;
    va_start(args, fmt);
    vsnprintf(ui.log[ui.log_count], sizeof ui.log[0], fmt, args);
    va_end(args);
    time_t now = time(NULL);
    struct tm *t = gmtime(&now);  // the 3DS clock is local time already
    snprintf(ui.log_time[ui.log_count], sizeof ui.log_time[0], "%02d:%02d", t->tm_hour, t->tm_min);
    ui.log_count++;
}

static void add_line(char level, const char *title, const char *detail) {
    if (ui.report_count == MAX_REPORT) return;
    Line *line = &ui.report[ui.report_count++];
    line->level = level;
    snprintf(line->title, sizeof line->title, "%s", title);
    snprintf(line->detail, sizeof line->detail, "%s", detail);
}

void ui_set_facts(const char *system, const char *luma, u64 sd_free, u64 sd_total) {
    if (ui.report_from_vcinject) return;
    ui.report_count = 0;
    add_line('i', "System", system);
    add_line('i', "Luma3DS", luma);
    char sd[64];
    snprintf(sd, sizeof sd, "%.1f GB free of %.1f GB", sd_free / 1e9, sd_total / 1e9);
    add_line('i', "SD card", sd);
}

void ui_set_report(const char *text, u32 size) {
    ui.report_count = 0;
    ui.report_from_vcinject = true;
    ui.report_from[0] = 0;
    char line[256];
    u32 start = 0;
    for (u32 i = 0; i <= size; i++) {
        if (i < size && text[i] != '\n') continue;
        u32 len = i - start < sizeof line - 1 ? i - start : sizeof line - 1;
        memcpy(line, text + start, len);
        line[len] = 0;
        start = i + 1;
        char *title = strchr(line, '\t');
        if (!title) continue;
        *title++ = 0;
        char *detail = strchr(title, '\t');
        if (detail) *detail++ = 0;
        if (strcmp(line, "from") == 0) {
            snprintf(ui.report_from, sizeof ui.report_from, "%s", title);
        } else {
            add_line(line[0], title, detail ? detail : "");
        }
    }
}

// --- drawing helpers ---------------------------------------------------------------

static void rounded(float x, float y, float w, float h, float r, u32 color) {
    C2D_DrawRectSolid(x + r, y, 0, w - 2 * r, h, color);
    C2D_DrawRectSolid(x, y + r, 0, r, h - 2 * r, color);
    C2D_DrawRectSolid(x + w - r, y + r, 0, r, h - 2 * r, color);
    C2D_DrawCircleSolid(x + r, y + r, 0, r, color);
    C2D_DrawCircleSolid(x + w - r, y + r, 0, r, color);
    C2D_DrawCircleSolid(x + r, y + h - r, 0, r, color);
    C2D_DrawCircleSolid(x + w - r, y + h - r, 0, r, color);
}

static void card(float x, float y, float w, float h) {
    rounded(x, y, w, h, 8, CARD_EDGE);
    rounded(x + 1, y + 1, w - 2, h - 2, 7, CARD);
}

// draws text cut to max_w (0: no limit); returns its width
static float text(const char *str, float x, float y, float size, u32 color, u32 flags, float max_w) {
    char buf[128];
    snprintf(buf, sizeof buf, "%s", str);
    C2D_Text t;
    float w = 0, h = 0;
    for (;;) {
        C2D_TextParse(&t, ui.text, buf);
        C2D_TextGetDimensions(&t, size, size, &w, &h);
        size_t len = strlen(buf);
        if (!max_w || w <= max_w || len <= 4) break;
        strcpy(buf + len - 4, "...");  // shorten until it fits
    }
    C2D_TextOptimize(&t);
    C2D_DrawText(&t, C2D_WithColor | flags, x, y, 0, size, size, color);
    return w;
}

static void badge(float x, float y, char level) {
    u32 color = level == 'o' ? GREEN : level == 'u' ? BLUE : level == 'w' ? ORANGE
              : level == 'e' ? RED : MUTED;
    C2D_DrawCircleSolid(x, y, 0, 7, color);
    if (level == 'o') {  // check mark
        C2D_DrawLine(x - 3.5f, y, TEXT, x - 1, y + 3, TEXT, 2, 0);
        C2D_DrawLine(x - 1, y + 3, TEXT, x + 4, y - 3, TEXT, 2, 0);
    } else if (level == 'u') {  // arrow up
        C2D_DrawTriangle(x, y - 4.5f, TEXT, x - 4, y, TEXT, x + 4, y, TEXT, 0);
        C2D_DrawRectSolid(x - 1.2f, y, 0, 2.4f, 4, TEXT);
    } else {
        text(level == 'w' || level == 'e' ? "!" : "i", x, y - 7, 0.5f, TEXT, C2D_AlignCenter, 0);
    }
}

static void status_bar(void) {
    C2D_DrawRectangle(0, 0, 0, TOP_W, 34, PURPLE, C(96, 72, 182), C(70, 52, 140), C(58, 44, 120));
    // logo: a little cartridge with "VC"
    rounded(10, 6, 22, 22, 5, TEXT);
    rounded(12, 8, 18, 12, 3, PURPLE);
    text("VC", 21, 8, 0.38f, TEXT, C2D_AlignCenter, 0);
    text("vcinject", 40, 6, 0.62f, TEXT, 0, 0);
    float w = text("Link", 101, 6, 0.62f, PURPLE_LIGHT, 0, 0);
    text("v" VERSION, 101 + w + 6, 11, 0.45f, C(205, 195, 245), 0, 0);

    float x = TOP_W - 12;
    time_t now = time(NULL);
    struct tm *t = gmtime(&now);
    char clock[8];
    snprintf(clock, sizeof clock, "%02d:%02d", t->tm_hour, t->tm_min);
    x -= text(clock, x, 8, 0.55f, TEXT, C2D_AlignRight, 0) + 12;

    // battery
    u8 level = 0, charging = 0;
    PTMU_GetBatteryLevel(&level);
    PTMU_GetBatteryChargeState(&charging);
    float bx = x - 26;
    rounded(bx, 11, 24, 12, 2, TEXT);
    C2D_DrawRectSolid(bx + 24, 14, 0, 2, 6, TEXT);
    C2D_DrawRectSolid(bx + 2, 13, 0, 20, 8, C(58, 44, 120));
    u32 fill = level <= 1 ? RED : charging ? GREEN : TEXT;
    C2D_DrawRectSolid(bx + 3, 14, 0, 18.f * (level > 5 ? 5 : level) / 5, 6, fill);
    x = bx - 12;

    // Wi-Fi: three bars
    u8 strength = ui.network_ok ? osGetWifiStrength() : 0;
    for (int i = 0; i < 3; i++) {
        float bh = 5 + i * 4;
        C2D_DrawRectSolid(x - 18 + i * 6, 24 - bh, 0, 4, bh, i < strength ? TEXT : C(150, 130, 210));
    }
}

static void top_screen(void) {
    C2D_TargetClear(ui.top, BG_TOP);
    C2D_SceneBegin(ui.top);
    status_bar();

    // what is happening
    card(10, 42, TOP_W - 20, 70);
    bool contact = ui.contact_ms && osGetTime() - ui.contact_ms < 15000;
    float pulse = 0.5f + 0.5f * sinf(ui.frame * 0.08f);
    u32 dot = !ui.network_ok ? RED : ui.activity[0] || contact ? GREEN
            : C2D_Color32(118, 92, 206, (u8)(90 + 165 * pulse));
    C2D_DrawCircleSolid(30, 64, 0, 7, dot);
    const char *headline;
    char line[96];
    if (!ui.network_ok) {
        headline = "Waiting for Wi-Fi";
    } else if (ui.activity[0]) {
        headline = ui.activity;
    } else if (contact) {
        snprintf(line, sizeof line, "Connected to vcinject");
        headline = line;
    } else {
        headline = "Waiting for vcinject";
    }
    text(headline, 46, 53, 0.65f, TEXT, 0, TOP_W - 76);
    char sub[96];
    if (!ui.network_ok) {
        snprintf(sub, sizeof sub, "It connects by itself once Wi-Fi is on.");
    } else if (contact) {
        snprintf(sub, sizeof sub, "%s%s%s", ui.report_from[0] ? ui.report_from : "computer",
                 ui.report_from[0] ? " - " : " ", ui.peer);
    } else {
        snprintf(sub, sizeof sub, "On your computer: vcinject > My 3DS...");
    }
    if (ui.progress >= 0) {
        float w = TOP_W - 76;
        rounded(46, 86, w, 10, 5, CARD_EDGE);
        if (ui.progress > 0.02f) rounded(46, 86, w * (ui.progress > 1 ? 1 : ui.progress), 10, 5, PURPLE_LIGHT);
        char pct[8];
        snprintf(pct, sizeof pct, "%d %%", (int)(ui.progress * 100));
        text(pct, TOP_W - 22, 72, 0.45f, MUTED, C2D_AlignRight, 0);
    } else {
        text(sub, 46, 80, 0.48f, MUTED, 0, TOP_W - 76);
    }

    // status report
    float y = 120;
    card(10, y, TOP_W - 20, H - y - 8);
    text(ui.report_from_vcinject ? "Status report" : "This 3DS", 22, y + 6, 0.5f, PURPLE_LIGHT, 0, 0);
    if (!ui.report_from_vcinject)
        text("vcinject checks for updates when it connects", TOP_W - 22, y + 8, 0.4f, MUTED,
             C2D_AlignRight, 0);
    y += 28;
    int rows = ui.report_count < 5 ? ui.report_count : 5;
    for (int i = 0; i < rows; i++, y += 17) {
        Line *l = &ui.report[i];
        badge(30, y + 7, l->level);
        float w = text(l->title, 44, y, 0.5f, TEXT, 0, 130);
        (void)w;
        text(l->detail, TOP_W - 22, y + 1, 0.45f, l->level == 'u' ? BLUE : l->level == 'w' ? ORANGE : MUTED,
             C2D_AlignRight, TOP_W - 200);
    }
    if (ui.report_count > rows) {
        char more[32];
        snprintf(more, sizeof more, "+%d more in vcinject", ui.report_count - rows);
        text(more, TOP_W - 22, H - 22, 0.4f, MUTED, C2D_AlignRight, 0);
    }
}

static void bottom_screen(void) {
    C2D_TargetClear(ui.bottom, BG_BOTTOM);
    C2D_SceneBegin(ui.bottom);
    text("Activity", 14, 10, 0.55f, PURPLE_LIGHT, 0, 0);
    float y = 34;
    if (!ui.log_count) text("Nothing yet. vcinject finds this 3DS by itself.", 14, y, 0.45f, MUTED, 0, BOTTOM_W - 28);
    for (int i = 0; i < ui.log_count; i++, y += 20) {
        text(ui.log_time[i], 14, y, 0.42f, MUTED, 0, 0);
        text(ui.log[i], 54, y, 0.45f, TEXT, 0, BOTTOM_W - 66);
    }

    card(8, H - 58, BOTTOM_W - 16, 50);
    text("Address", 20, H - 52, 0.42f, MUTED, 0, 0);
    text(ui.network_ok ? ui.address : "-", 20, H - 36, 0.6f, TEXT, 0, 0);
    text("START", BOTTOM_W - 20, H - 52, 0.42f, MUTED, C2D_AlignRight, 0);
    text("Exit", BOTTOM_W - 20, H - 36, 0.6f, TEXT, C2D_AlignRight, 0);
}

void ui_render(void) {
    ui.frame++;
    C2D_TextBufClear(ui.text);
    C3D_FrameBegin(C3D_FRAME_SYNCDRAW);
    top_screen();
    bottom_screen();
    C3D_FrameEnd(0);
}
