/* Reading the game's credit out of a version 7 CPK archive, in memory only. */
#ifndef HU_CPK_H
#define HU_CPK_H

#include <windows.h>

typedef unsigned long long u64;
typedef unsigned int u32;

typedef struct hu_archive hu_archive;

/* Parses `path`, finds the credit entry, and builds the rebuilt sectors. Returns NULL when the
 * archive is not one this package knows, or the credit is not there (already replaced, another
 * build, an encrypted chunk). Free with hu_cpk_free(). */
hu_archive *hu_cpk_prepare(const WCHAR *path, const char *old_text, const char *new_text);

void hu_cpk_free(hu_archive *archive);

/* Writes a trace line when HU_LOG is set in the environment. */
void hu_log(const char *format, const char *text);

/* Overwrites the bytes of `buffer` that came from a rebuilt sector. True when it wrote. */
BOOL hu_cpk_patch_read(hu_archive *archive, LONGLONG offset, unsigned char *buffer, DWORD bytes);

#endif
