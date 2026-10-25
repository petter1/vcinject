// The screens of vcinject Link (citro2d).
#pragma once

#include <3ds.h>

void ui_init(void);
void ui_exit(void);
// draws both screens; waits for the next frame
void ui_render(void);

void ui_set_address(u32 ip, bool network_ok);
// a computer talked to us (shows "Connected to ...")
void ui_contact(const char *peer);
// what is going on right now; progress 0..1, or < 0 for none; NULL text clears it
void ui_set_activity(const char *text, float progress);
// a line in the activity log on the bottom screen
void ui_log(const char *fmt, ...);
// the status report vcinject sends: lines of "level\ttitle\tdetail" with level
// ok, update, warn or info; the first line may be "from\t<computer name>"
void ui_set_report(const char *text, u32 size);
// what the app knows itself, shown until vcinject sends its report
void ui_set_facts(const char *system, const char *luma, u64 sd_free, u64 sd_total);
