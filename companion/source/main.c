// vcinject Link: lets vcinject on a computer in the same Wi-Fi find this 3DS,
// list and install titles and read or write files (e.g. the HOME Menu layout).
//
// The app only moves raw data; vcinject parses it. Protocol (little endian):
//   discovery: UDP datagram "vcinject?" to port 34013 -> reply "vcinject-link\n" + info
//   request:   u32 command, u32 length, payload
//   response:  u32 result (0 = ok, else a 3DS Result code), u32 length, payload
#include <3ds.h>
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <malloc.h>
#include <netinet/in.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "ui.h"
#include "version.h"

#define PORT 34013
#define SOC_BUFFER_SIZE 0x100000
#define CHUNK 0x4000
#define MAX_PAYLOAD (8 * 1024 * 1024)  // except for installs, which are streamed

enum {
    CMD_INFO = 1,
    CMD_TITLES = 2,
    CMD_SMDH = 3,
    CMD_READ_FILE = 4,
    CMD_WRITE_FILE = 5,
    CMD_LIST_DIR = 6,
    CMD_INSTALL = 7,
    CMD_DELETE = 8,
    CMD_REBOOT = 9,
    CMD_REPORT = 10,  // the status report to show (see ui_set_report)
    CMD_NOTE = 11,    // a line for the activity log, e.g. "Reading the HOME Menu"
    CMD_SMDHS = 12,   // the SMDHs of many titles at once (one round trip instead of many)
    CMD_PROGRESS = 13,  // u32 done, u32 total, text: what vcinject is doing, shown with a bar
    CMD_RESTART = 14,   // quit and start again, e.g. as the version just installed
    // 15-17 were experiments with the HOME Menu's process, now unused
    CMD_LAUNCH = 18,    // quit and start a title: u8 media, u64 title ID
};

#define ERR_BAD_REQUEST 0xE0000001
#define ERR_NO_MEMORY 0xE0000002
#define ERR_TOO_LARGE 0xE0000003
#define ERR_UNKNOWN_COMMAND 0xE0000004

static bool reboot_requested, restart_requested;
static u64 launch_title;  // started when we quit (restart: ourselves)
static u8 launch_media = MEDIATYPE_SD;

// --- buffers ---------------------------------------------------------------

typedef struct {
    u8 *data;
    u32 size, cap;
} Buffer;

static bool buf_put(Buffer *b, const void *data, u32 size) {
    if (b->size + size > b->cap) {
        u32 cap = b->cap ? b->cap : 4096;
        while (cap < b->size + size) cap *= 2;
        u8 *grown = realloc(b->data, cap);
        if (!grown) return false;
        b->data = grown;
        b->cap = cap;
    }
    memcpy(b->data + b->size, data, size);
    b->size += size;
    return true;
}

static bool buf_printf(Buffer *b, const char *fmt, ...) {
    char line[256];
    va_list args;
    va_start(args, fmt);
    int n = vsnprintf(line, sizeof line, fmt, args);
    va_end(args);
    return n > 0 && buf_put(b, line, (u32)n < sizeof line ? (u32)n : sizeof line - 1);
}

// reads little-endian fields from a request payload
typedef struct {
    const u8 *data;
    u32 size, pos;
    bool bad;
} Reader;

static const void *take(Reader *r, u32 size) {
    if (r->bad || r->size - r->pos < size) {
        r->bad = true;
        return NULL;
    }
    const void *at = r->data + r->pos;
    r->pos += size;
    return at;
}

static u8 take_u8(Reader *r) {
    const u8 *p = take(r, 1);
    return p ? *p : 0;
}

static u32 take_u32(Reader *r) {
    u32 v = 0;
    const void *p = take(r, 4);
    if (p) memcpy(&v, p, 4);
    return v;
}

static u64 take_u64(Reader *r) {
    u64 v = 0;
    const void *p = take(r, 8);
    if (p) memcpy(&v, p, 8);
    return v;
}

// an FS_Path as u32 type, u32 length, bytes (an empty one stands for PATH_EMPTY)
static FS_Path take_path(Reader *r) {
    u32 type = take_u32(r);
    u32 size = take_u32(r);
    const void *data = take(r, size);
    if (!data || type == PATH_EMPTY || size == 0) return (FS_Path){PATH_EMPTY, 1, ""};
    return (FS_Path){(FS_PathType)type, size, data};
}

// --- sockets ---------------------------------------------------------------

static bool send_all(int fd, const void *data, u32 size) {
    const u8 *p = data;
    while (size) {
        int n = send(fd, p, size < CHUNK ? size : CHUNK, 0);
        if (n <= 0) return false;
        p += n;
        size -= n;
    }
    return true;
}

static bool recv_all(int fd, void *data, u32 size) {
    u8 *p = data;
    while (size) {
        int n = recv(fd, p, size < CHUNK ? size : CHUNK, 0);
        if (n <= 0) return false;
        p += n;
        size -= n;
    }
    return true;
}

static bool respond(int fd, Result result, const void *data, u32 size) {
    u32 header[2] = {(u32)result, R_FAILED(result) ? 0 : size};
    return send_all(fd, header, sizeof header) && (R_FAILED(result) || send_all(fd, data, size));
}

static void set_blocking(int fd, bool blocking) {
    int flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, blocking ? flags & ~O_NONBLOCK : flags | O_NONBLOCK);
}

// --- commands --------------------------------------------------------------

static void info(Buffer *out) {
    u8 region = 0, model = 0;
    u16 name[0x1C / 2 + 1] = {0};
    char name8[64] = {0};
    bool new3ds = false;
    char sysver[64] = "?";
    OS_VersionBin nver, cver;

    CFGU_SecureInfoGetRegion(&region);
    CFGU_GetSystemModel(&model);
    if (R_SUCCEEDED(CFGU_GetConfigInfoBlk2(0x1C, 0x000A0000, name)))
        utf16_to_utf8((u8 *)name8, name, sizeof name8 - 1);
    u32 device_id = 0;
    s32 internal = 0;
    AM_GetDeviceId(&internal, &device_id);  // unique per console: vcinject keeps its data apart by it
    APT_CheckNew3DS(&new3ds);
    if (R_FAILED(osGetSystemVersionDataString(&nver, &cver, sysver, sizeof sysver)))
        strcpy(sysver, "?");
    char luma[16] = "";
    s64 luma_version = 0;  // Luma3DS answers this system info type with its version
    if (R_SUCCEEDED(svcGetSystemInfo(&luma_version, 0x10000, 0)))
        snprintf(luma, sizeof luma, "%lu.%lu.%lu", (u32)(luma_version >> 24) & 0xFF,
                 (u32)(luma_version >> 16) & 0xFF, (u32)(luma_version >> 8) & 0xFF);

    buf_printf(out, "app=vcinject-link\nversion=%s\nname=%s\nregion=%u\nmodel=%u\nnew3ds=%u\n",
               VERSION, name8, region, model, new3ds);
    buf_printf(out, "system=%s\nid=%08lX\nluma=%s\n", sysver, device_id, luma);
    u8 battery = 0, charging = 0;
    PTMU_GetBatteryLevel(&battery);
    PTMU_GetBatteryChargeState(&charging);
    buf_printf(out, "battery=%u\ncharging=%u\n", battery, charging);
    FS_ArchiveResource sd;
    u64 sd_free = 0, sd_total = 0;
    if (R_SUCCEEDED(FSUSER_GetArchiveResource(&sd, SYSTEM_MEDIATYPE_SD))) {
        sd_free = (u64)sd.freeClusters * sd.clusterSize;
        sd_total = (u64)sd.totalClusters * sd.clusterSize;
        buf_printf(out, "sd_free=%llu\nsd_total=%llu\n", sd_free, sd_total);
    }
    ui_set_facts(sysver, luma[0] ? luma : "not found", sd_free, sd_total);
}

// per title: u64 id, u64 size, u16 version, u8 media, u8 reserved[5], char product_code[16]
static Result titles(FS_MediaType media, bool product_codes, Buffer *out) {
    u32 count = 0;
    Result res = AM_GetTitleCount(media, &count);
    if (R_FAILED(res)) return media == MEDIATYPE_GAME_CARD ? 0 : res;  // no card inserted
    if (!count) return 0;
    u64 *ids = calloc(count, sizeof(u64));
    AM_TitleEntry *entries = calloc(count, sizeof(AM_TitleEntry));
    if (!ids || !entries) {
        free(ids);
        free(entries);
        return ERR_NO_MEMORY;
    }
    u32 read = 0;
    res = AM_GetTitleList(&read, media, count, ids);
    if (R_SUCCEEDED(res)) res = AM_GetTitleInfo(media, read, ids, entries);
    for (u32 i = 0; R_SUCCEEDED(res) && i < read; i++) {
        u8 record[40] = {0};
        memcpy(record, &entries[i].titleID, 8);
        memcpy(record + 8, &entries[i].size, 8);
        memcpy(record + 16, &entries[i].version, 2);
        record[18] = media;
        u32 category = entries[i].titleID >> 32;
        if (product_codes && (category == 0x00040000 || category == 0x00040002))  // one call each: slow
            AM_GetTitleProductCode(media, entries[i].titleID, (char *)record + 24);
        if (!buf_put(out, record, sizeof record)) res = ERR_NO_MEMORY;
    }
    free(ids);
    free(entries);
    return res;
}

static Result smdh(FS_MediaType media, u64 id, Buffer *out) {
    static const u32 icon_path[5] = {0, 0, 2, 0x6E6F6369 /* "icon" */, 0};
    u32 archive_path[4] = {(u32)id, (u32)(id >> 32), media, 0};
    Handle file;
    Result res = FSUSER_OpenFileDirectly(&file, ARCHIVE_SAVEDATA_AND_CONTENT,
                                         (FS_Path){PATH_BINARY, sizeof archive_path, archive_path},
                                         (FS_Path){PATH_BINARY, sizeof icon_path, icon_path},
                                         FS_OPEN_READ, 0);
    if (R_FAILED(res)) return res;
    u8 data[0x36C0];
    u32 read = 0;
    res = FSFILE_Read(file, &read, 0, data, sizeof data);
    FSFILE_Close(file);
    if (R_SUCCEEDED(res) && !buf_put(out, data, read)) res = ERR_NO_MEMORY;
    return res;
}

// payload: per title u8 media, u64 ID; response: per title u32 size (0: none), then the SMDH
static Result smdhs(Reader *r, Buffer *out) {
    while (r->pos < r->size) {
        u8 media = take_u8(r);
        u64 id = take_u64(r);
        if (r->bad) return ERR_BAD_REQUEST;
        u32 at = out->size, size = 0;
        if (!buf_put(out, &size, 4)) return ERR_NO_MEMORY;
        if (R_SUCCEEDED(smdh((FS_MediaType)media, id, out))) {
            size = out->size - at - 4;
            memcpy(out->data + at, &size, 4);
        }
    }
    return 0;
}

static Result read_file(Reader *r, Buffer *out) {
    FS_ArchiveID archive_id = take_u32(r);
    FS_Path archive_path = take_path(r);
    FS_Path file_path = take_path(r);
    if (r->bad) return ERR_BAD_REQUEST;
    FS_Archive archive;
    Result res = FSUSER_OpenArchive(&archive, archive_id, archive_path);
    if (R_FAILED(res)) return res;
    Handle file;
    res = FSUSER_OpenFile(&file, archive, file_path, FS_OPEN_READ, 0);
    if (R_SUCCEEDED(res)) {
        u64 size = 0;
        res = FSFILE_GetSize(file, &size);
        if (R_SUCCEEDED(res) && size > MAX_PAYLOAD) res = ERR_TOO_LARGE;
        u8 *data = R_SUCCEEDED(res) ? malloc(size ? size : 1) : NULL;
        if (R_SUCCEEDED(res) && !data) res = ERR_NO_MEMORY;
        u32 read = 0;
        if (R_SUCCEEDED(res)) res = FSFILE_Read(file, &read, 0, data, (u32)size);
        if (R_SUCCEEDED(res) && !buf_put(out, data, read)) res = ERR_NO_MEMORY;
        free(data);
        FSFILE_Close(file);
    }
    FSUSER_CloseArchive(archive);
    return res;
}

// replaces a whole file (save files may be fixed size: the size is set only when it differs)
static Result write_into(FS_Archive archive, FS_Path file_path, const void *data, u32 size) {
    Handle file;
    // extdata refuses FS_OPEN_CREATE: open what is there, create only what is missing
    Result res = FSUSER_OpenFile(&file, archive, file_path, FS_OPEN_WRITE, 0);
    if (R_FAILED(res)) {
        if (R_FAILED(FSUSER_CreateFile(archive, file_path, 0, size))) return res;
        res = FSUSER_OpenFile(&file, archive, file_path, FS_OPEN_WRITE, 0);
        if (R_FAILED(res)) return res;
    }
    u64 old_size = 0;
    FSFILE_GetSize(file, &old_size);
    if (old_size != size) res = FSFILE_SetSize(file, size);
    u32 written = 0;
    if (R_SUCCEEDED(res)) res = FSFILE_Write(file, &written, 0, data, size, FS_WRITE_FLUSH);
    FSFILE_Close(file);
    return res;
}

// payload: archive, file path, u32 flags (1 = commit save data), then the file's bytes
static Result write_file(Reader *r) {
    FS_ArchiveID archive_id = take_u32(r);
    FS_Path archive_path = take_path(r);
    FS_Path file_path = take_path(r);
    u32 flags = take_u32(r);
    if (r->bad) return ERR_BAD_REQUEST;
    u32 size = r->size - r->pos;
    const void *data = take(r, size);
    FS_Archive archive;
    Result res = FSUSER_OpenArchive(&archive, archive_id, archive_path);
    if (R_FAILED(res)) return res;
    res = write_into(archive, file_path, data, size);
    if (R_SUCCEEDED(res) && (flags & 1))
        res = FSUSER_ControlArchive(archive, ARCHIVE_ACTION_COMMIT_SAVE_DATA, NULL, 0, NULL, 0);
    FSUSER_CloseArchive(archive);
    return res;
}

// per entry: u8 is_dir, u8 reserved[3], u64 size, u32 name length, UTF-8 name
static Result list_dir(Reader *r, Buffer *out) {
    FS_ArchiveID archive_id = take_u32(r);
    FS_Path archive_path = take_path(r);
    FS_Path dir_path = take_path(r);
    if (r->bad) return ERR_BAD_REQUEST;
    FS_Archive archive;
    Result res = FSUSER_OpenArchive(&archive, archive_id, archive_path);
    if (R_FAILED(res)) return res;
    Handle dir;
    res = FSUSER_OpenDirectory(&dir, archive, dir_path);
    if (R_SUCCEEDED(res)) {
        FS_DirectoryEntry entry;
        u32 count = 0;
        while (R_SUCCEEDED(res = FSDIR_Read(dir, &count, 1, &entry)) && count == 1) {
            u8 name[0x106 * 3 + 1] = {0};
            ssize_t len = utf16_to_utf8(name, entry.name, sizeof name - 1);
            u8 head[16] = {0};
            head[0] = (entry.attributes & FS_ATTRIBUTE_DIRECTORY) ? 1 : 0;
            memcpy(head + 4, &entry.fileSize, 8);
            u32 name_len = len > 0 ? (u32)len : 0;
            memcpy(head + 12, &name_len, 4);
            if (!buf_put(out, head, sizeof head) || !buf_put(out, name, name_len)) {
                res = ERR_NO_MEMORY;
                break;
            }
        }
        FSDIR_Close(dir);
    }
    FSUSER_CloseArchive(archive);
    return res;
}

// streams the CIA from the socket into AM; the payload is u8 media, then the CIA
static Result install(int fd, u32 size, bool *connection_ok) {
    u8 media;
    if (size < 1 || !recv_all(fd, &media, 1)) return *connection_ok = false, ERR_BAD_REQUEST;
    size -= 1;
    u8 *chunk = malloc(0x20000);
    if (!chunk) return ERR_NO_MEMORY;  // the stream is left unread: the connection ends
    Handle cia;
    Result res = AM_StartCiaInstall((FS_MediaType)media, &cia);
    u64 offset = 0, total = size;
    ui_log("Installing a title (%.1f MB)", size / 1e6);
    while (size) {
        ui_set_activity("Installing...", (float)offset / total);
        ui_render();
        u32 n = size < 0x20000 ? size : 0x20000;
        if (!recv_all(fd, chunk, n)) {
            *connection_ok = false;
            if (R_SUCCEEDED(res)) res = ERR_BAD_REQUEST;
            break;
        }
        u32 written = 0;
        if (R_SUCCEEDED(res)) res = FSFILE_Write(cia, &written, offset, chunk, n, 0);
        offset += n;
        size -= n;
    }
    free(chunk);
    if (R_SUCCEEDED(res)) {
        res = AM_FinishCiaInstall(cia);
    } else if (cia) {
        AM_CancelCIAInstall(cia);
    }
    ui_set_activity(NULL, -1);
    if (R_SUCCEEDED(res)) {
        ui_log("Installed.");
    } else {
        ui_log("Installing failed (%08lX).", res);
    }
    return res;
}

// handles one request; false when the connection should end
static bool handle(int fd) {
    u32 header[2];
    if (!recv_all(fd, header, sizeof header)) return false;
    u32 command = header[0], size = header[1];
    if (command == CMD_INSTALL) {
        bool connection_ok = true;
        Result res = install(fd, size, &connection_ok);
        return connection_ok && respond(fd, res, NULL, 0);
    }
    if (size > MAX_PAYLOAD) {
        respond(fd, ERR_TOO_LARGE, NULL, 0);
        return false;
    }
    u8 *payload = malloc(size ? size : 1);
    if (!payload) return false;
    if (!recv_all(fd, payload, size)) {
        free(payload);
        return false;
    }
    Reader r = {payload, size, 0, false};
    Buffer out = {0};
    Result res = 0;
    switch (command) {
    case CMD_INFO:
        info(&out);
        break;
    case CMD_TITLES: {
        u8 media = take_u8(&r);
        bool product_codes = r.size < 2 || take_u8(&r);  // vcinject before Link 4 always wanted them
        res = r.bad ? ERR_BAD_REQUEST : titles((FS_MediaType)media, product_codes, &out);
        break;
    }
    case CMD_SMDH: {
        u8 media = take_u8(&r);
        u64 id = take_u64(&r);
        res = r.bad ? ERR_BAD_REQUEST : smdh((FS_MediaType)media, id, &out);
        break;
    }
    case CMD_READ_FILE:
        res = read_file(&r, &out);  // failures are vcinject's to report (some are expected)
        break;
    case CMD_WRITE_FILE:
        res = write_file(&r);
        if (R_FAILED(res)) ui_log("Writing a file failed (%08lX).", res);
        break;
    case CMD_LIST_DIR:
        res = list_dir(&r, &out);
        break;
    case CMD_DELETE: {
        u8 media = take_u8(&r);
        u64 id = take_u64(&r);
        res = r.bad ? ERR_BAD_REQUEST : AM_DeleteTitle((FS_MediaType)media, id);
        if (R_SUCCEEDED(res)) {
            ui_log("Deleted %016llX.", id);
        } else {
            ui_log("Deleting %016llX failed (%08lX).", id, res);
        }
        break;
    }
    case CMD_REBOOT:
        ui_set_activity("Restarting the 3DS...", -1);
        ui_render();
        reboot_requested = true;
        break;
    case CMD_LAUNCH: {
        u8 media = take_u8(&r);
        u64 id = take_u64(&r);
        if (r.bad) {
            res = ERR_BAD_REQUEST;
            break;
        }
        ui_set_activity("Starting the title...", -1);
        ui_render();
        launch_title = id;
        launch_media = media;
        restart_requested = true;
        break;
    }
    case CMD_RESTART:
        ui_set_activity("Restarting vcinject Link...", -1);
        ui_render();
        restart_requested = true;
        break;
    case CMD_REPORT:
        ui_set_report((const char *)payload, size);
        break;
    case CMD_SMDHS:
        res = smdhs(&r, &out);
        break;
    case CMD_PROGRESS: {
        u32 done = take_u32(&r), total = take_u32(&r);
        char text[64];
        u32 len = r.size - r.pos < sizeof text - 1 ? r.size - r.pos : sizeof text - 1;
        memcpy(text, r.data + r.pos, len);
        text[len] = 0;
        ui_set_activity(len ? text : NULL, total ? (float)done / total : -1);
        break;
    }
    case CMD_NOTE: {
        char note[64];
        snprintf(note, sizeof note, "%.*s", (int)(size < sizeof note - 1 ? size : sizeof note - 1),
                 (const char *)payload);
        ui_log("%s", note);
        break;
    }
    default:
        res = ERR_UNKNOWN_COMMAND;
    }
    bool ok = respond(fd, res, out.data, out.size);
    free(out.data);
    free(payload);
    return ok && !reboot_requested && !restart_requested;
}

// --- discovery ---------------------------------------------------------------

static void answer_discovery(int udp) {
    char query[32];
    struct sockaddr_in from;
    socklen_t from_len = sizeof from;
    int n = recvfrom(udp, query, sizeof query - 1, 0, (struct sockaddr *)&from, &from_len);
    if (n <= 0) return;
    query[n] = 0;
    if (strcmp(query, "vcinject?") != 0) return;
    Buffer reply = {0};
    buf_printf(&reply, "vcinject-link\nport=%d\n", PORT);
    info(&reply);
    sendto(udp, reply.data, reply.size, 0, (struct sockaddr *)&from, from_len);
    free(reply.data);
}

// --- main --------------------------------------------------------------------

static int open_socket(int type) {
    int fd = socket(AF_INET, type, 0);
    if (fd < 0) return -1;
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(PORT);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, (struct sockaddr *)&addr, sizeof addr) < 0 ||
        (type == SOCK_STREAM && listen(fd, 2) < 0)) {
        close(fd);
        return -1;
    }
    set_blocking(fd, false);
    return fd;
}

int main(void) {
    gfxInitDefault();
    amInit();
    cfguInit();
    ui_init();
    u32 *soc_buffer = memalign(0x1000, SOC_BUFFER_SIZE);
    bool network = soc_buffer && R_SUCCEEDED(socInit(soc_buffer, SOC_BUFFER_SIZE));
    int tcp = -1, udp = -1;
    u32 address = 0;
    u64 address_checked = 0;
    Buffer facts = {0};
    info(&facts);  // shows what the 3DS knows about itself
    free(facts.data);
    ui_log("vcinject Link " VERSION " started");

    while (aptMainLoop() && !reboot_requested && !restart_requested) {
        hidScanInput();
        if (hidKeysDown() & KEY_START) break;
        // Wi-Fi may connect only after the app started, or the address may change:
        // (re)open the sockets whenever the 3DS gets a new address
        if (network && osGetTime() - address_checked > 1000) {
            address_checked = osGetTime();
            u32 now = (u32)gethostid();
            if (now == 0xFFFFFFFF) now = 0;  // no Wi-Fi yet
            if (now != address || (now && (tcp < 0 || udp < 0))) {
                if (tcp >= 0) close(tcp);
                if (udp >= 0) close(udp);
                tcp = now ? open_socket(SOCK_STREAM) : -1;
                udp = now ? open_socket(SOCK_DGRAM) : -1;
                if (now && address != now) ui_log(address ? "New address: %s" : "Wi-Fi connected: %s",
                                                  inet_ntoa((struct in_addr){now}));
                address = now;
                ui_set_address(now, now && tcp >= 0 && udp >= 0);
            }
        }
        if (udp >= 0) answer_discovery(udp);
        if (tcp >= 0) {
            struct sockaddr_in client;
            socklen_t client_len = sizeof client;
            int fd = accept(tcp, (struct sockaddr *)&client, &client_len);
            if (fd >= 0) {
                set_blocking(fd, true);
                ui_contact(inet_ntoa(client.sin_addr));
                u64 drawn = osGetTime();
                while (handle(fd)) {
                    ui_contact(inet_ntoa(client.sin_addr));
                    if (osGetTime() - drawn > 50) {  // keep the screens alive during long sessions
                        ui_render();
                        drawn = osGetTime();
                    }
                }
                close(fd);
                ui_set_activity(NULL, -1);
            }
        }
        ui_render();
    }

    if (tcp >= 0) close(tcp);
    if (udp >= 0) close(udp);
    if (network) socExit();
    ui_exit();
    cfguExit();
    amExit();
    gfxExit();
    if (reboot_requested) APT_HardwareResetAsync();
    if (restart_requested) {  // the HOME Menu starts the title (or us again) when we quit
        u64 self = 0;
        if (launch_title) {
            aptSetChainloader(launch_title, launch_media);
        } else if (R_SUCCEEDED(APT_GetProgramID(&self))) {
            aptSetChainloader(self, MEDIATYPE_SD);
        }
    }
    return 0;
}
