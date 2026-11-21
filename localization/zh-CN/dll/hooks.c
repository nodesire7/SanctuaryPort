/* The localization hook that rides along with the SDL3 proxy.
 *
 * The port starts as it ships: SanctuaryPort.exe and bin\sanctuary-probe.exe both load
 * bin\SDL3.dll, which this project replaces with a proxy. The proxy forwards every SDL3 export
 * to the real library (SDL3.real.dll - the original file under its new name) and calls
 * hu_install_hooks() once per process, where this file takes over:
 *
 *   - the launcher reads its UI texts, trophy names and the update notes from files next to the
 *     executable. Those reads are redirected to the translated copies this package ships, so no
 *     file of the port is edited and an upstream update can never conflict with a translation.
 *
 * Nothing patches instruction bytes here: the import table entries that point at CreateFileW
 * are repointed at this DLL, in every module that imports it - the launcher (Rust), the probe
 * (C++ over msvcrt) and their helpers.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include "cpk.h"

#define HU_DATA_DIR L"zh-CN"

typedef HANDLE(WINAPI *create_file_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD,
                                      DWORD, HANDLE);
typedef BOOL(WINAPI *read_file_t)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef BOOL(WINAPI *set_pointer_t)(HANDLE, LARGE_INTEGER, PLARGE_INTEGER, DWORD);

static create_file_t real_create_file;
static read_file_t real_read_file;
static set_pointer_t real_set_pointer;

/* The credit line the game shows, replaced in the bytes it reads. Both are 36 bytes. */
static const char CREDIT_OLD[] = "GKinto.com 游戏大桶 汉化制作";
static const char CREDIT_NEW[] = "bbs.GBTGame.me 海量游戏尽在GBT";
#define CREDIT_ARCHIVE L"\\enUS_Patch2_7_7.cpk" /* the name is matched after the last separator */

/* Handles opened on that archive, with the position the caller is reading at. */
#define TRACKED 16
static struct {
    HANDLE handle;
    LONGLONG position;
} tracked[TRACKED];
static hu_archive *archive;
static LONG archive_state; /* 0: not looked for, 1: looked for, 2: found */

static WCHAR module_dir[MAX_PATH]; /* ...\bin\ */
static WCHAR data_dir[MAX_PATH];   /* ...\bin\zh-CN\ */
static LONG calls;
static __thread int inside_hook;

static HANDLE WINAPI hook_create_file(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD,
                                      HANDLE);
static void patch_everywhere(void);

/* ---------------------------------------------------------------- helpers */

/* The port passes `...\CUSA00242-UPDATE/CPKs/x.cpk` (its own path joins use '/'), so a suffix
 * match has to accept either separator. */
static BOOL ends_with_ci(const WCHAR *text, const WCHAR *suffix) {
    size_t have = lstrlenW(text), want = lstrlenW(suffix);
    if (want > have) return FALSE;
    return lstrcmpiW(text + (have - want), suffix) == 0;
}

/* The file name after the last '\' or '/', or the whole path when it has neither. */
static const WCHAR *file_name(const WCHAR *path) {
    const WCHAR *at = path, *name = path;
    for (; *at; at++) {
        if (*at == L'\\' || *at == L'/') name = at + 1;
    }
    return name;
}

/* The translated copy of `path` when this package ships one, else NULL. */
static const WCHAR *translated_for(const WCHAR *path) {
    static WCHAR candidate[MAX_PATH];
    const WCHAR *name;

    if (ends_with_ci(path, L"assets/i18n.json") || ends_with_ci(path, L"assets\\i18n.json")) {
        name = L"i18n.json";
    } else if (ends_with_ci(path, L"trophies/Diablo_III.json") || ends_with_ci(path, L"trophies\\Diablo_III.json")) {
        name = L"trophies.json";
    } else if (lstrcmpiW(file_name(path), L"changeinfo.xml") == 0) {
        name = L"changeinfo.xml";
    } else {
        return NULL;
    }
    lstrcpyW(candidate, data_dir);
    lstrcatW(candidate, name);
    return GetFileAttributesW(candidate) == INVALID_FILE_ATTRIBUTES ? NULL : candidate;
}

/* ------------------------------------------------------------ handle tracking */

/* Remembers a handle opened on the credit archive, preparing the rebuilt sectors once. */
static void track(HANDLE handle, const WCHAR *path) {
    int slot;
    if (!path || lstrcmpiW(file_name(path), CREDIT_ARCHIVE + 1) != 0) return;
    if (InterlockedCompareExchange(&archive_state, 1, 0) == 0) {
        hu_archive *found = hu_cpk_prepare(path, CREDIT_OLD, CREDIT_NEW);
        archive = found;
        InterlockedExchange(&archive_state, found ? 2 : 1);
    }
    if (!archive) return;
    for (slot = 0; slot < TRACKED; slot++) {
        if (tracked[slot].handle == NULL || tracked[slot].handle == INVALID_HANDLE_VALUE) {
            tracked[slot].handle = handle;
            tracked[slot].position = 0;
            return;
        }
    }
}

static int tracked_slot(HANDLE handle) {
    int slot;
    for (slot = 0; slot < TRACKED; slot++) {
        if (tracked[slot].handle == handle) return slot;
    }
    return -1;
}

/* ------------------------------------------------------------------- hooks */

static BOOL WINAPI hook_read_file(HANDLE file, LPVOID buffer, DWORD bytes, LPDWORD read,
                                  LPOVERLAPPED overlapped) {
    BOOL ok = real_read_file(file, buffer, bytes, read, overlapped);
    int slot = tracked_slot(file);

    if (ok && read && *read && slot >= 0 && archive && !overlapped) {

        hu_cpk_patch_read(archive, tracked[slot].position, (unsigned char *)buffer, *read);
        tracked[slot].position += *read;
    }
    return ok;
}

static BOOL WINAPI hook_set_pointer(HANDLE file, LARGE_INTEGER distance, PLARGE_INTEGER after,
                                    DWORD method) {
    BOOL ok = real_set_pointer(file, distance, after, method);
    int slot = tracked_slot(file);
    if (ok && slot >= 0) {
        /* The caller may pass no output pointer, so the new position is computed here. */
        if (after) {
            tracked[slot].position = after->QuadPart;
        } else if (method == FILE_BEGIN) {
            tracked[slot].position = distance.QuadPart;
        } else if (method == FILE_CURRENT) {
            tracked[slot].position += distance.QuadPart;
        } else if (method == FILE_END) {
            LARGE_INTEGER size;
            if (GetFileSizeEx(file, &size)) tracked[slot].position = size.QuadPart + distance.QuadPart;
        }
    }

    if (ok && slot >= 0 && after) tracked[slot].position = after->QuadPart;
    return ok;
}

/* ------------------------------------------------------------ import patch */

static void patch_module(HMODULE module, const char *name, void *replacement) {
    BYTE *base = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return;
    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;
    IMAGE_DATA_DIRECTORY imports = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!imports.VirtualAddress) return;

    IMAGE_IMPORT_DESCRIPTOR *descriptor =
        (IMAGE_IMPORT_DESCRIPTOR *)(base + imports.VirtualAddress);
    for (; descriptor->Name; descriptor++) {
        IMAGE_THUNK_DATA *thunks = descriptor->OriginalFirstThunk
                                       ? (IMAGE_THUNK_DATA *)(base + descriptor->OriginalFirstThunk)
                                       : (IMAGE_THUNK_DATA *)(base + descriptor->FirstThunk);
        IMAGE_THUNK_DATA *slot = (IMAGE_THUNK_DATA *)(base + descriptor->FirstThunk);
        for (; thunks->u1.AddressOfData; thunks++, slot++) {
            IMAGE_IMPORT_BY_NAME *by_name;
            DWORD previous = 0;
            if (thunks->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            by_name = (IMAGE_IMPORT_BY_NAME *)(base + thunks->u1.AddressOfData);
            if (lstrcmpA((LPCSTR)by_name->Name, name) != 0) continue;
            if (!VirtualProtect(&slot->u1.Function, sizeof(void *), PAGE_READWRITE, &previous)) {
                continue;
            }
            slot->u1.Function = (ULONG_PTR)replacement;
            VirtualProtect(&slot->u1.Function, sizeof(void *), previous, &previous);
        }
    }
}

/* Every module of the process, so the CRT's own imports are covered too. */
static void patch_everywhere(void) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, GetCurrentProcessId());
    MODULEENTRY32W entry;
    if (snapshot == INVALID_HANDLE_VALUE) return;
    entry.dwSize = sizeof(entry);
    if (Module32FirstW(snapshot, &entry)) {
        do {
            HMODULE module = (HMODULE)(LPVOID)entry.hModule;
            /* Ourselves, and the system libraries whose own imports would call back into the
               hook from inside GetFileAttributesW or the loader. */
            if (lstrcmpiW(entry.szModule, L"SDL3.dll") == 0) continue;
            if (lstrcmpiW(entry.szModule, L"kernel32.dll") == 0) continue;
            if (lstrcmpiW(entry.szModule, L"kernelbase.dll") == 0) continue;
            if (lstrcmpiW(entry.szModule, L"ntdll.dll") == 0) continue;
            patch_module(module, "CreateFileW", (void *)&hook_create_file);
            patch_module(module, "ReadFile", (void *)&hook_read_file);
            patch_module(module, "SetFilePointerEx", (void *)&hook_set_pointer);
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
}

/* ------------------------------------------------------------------- hooks */

static HANDLE WINAPI hook_create_file(LPCWSTR path, DWORD access, DWORD share,
                                      LPSECURITY_ATTRIBUTES attributes, DWORD creation,
                                      DWORD flags, HANDLE template_file) {
    const WCHAR *translated;
    HANDLE result;
    /* Our own lookup opens files too: keep it from coming back through this hook. */
    if (inside_hook) {
        return real_create_file(path, access, share, attributes, creation, flags, template_file);
    }
    inside_hook = 1;
    /* The first call runs outside the loader lock (a module snapshot is not safe inside
       DllMain), so the rest of the process is patched there, once. */
    if (InterlockedIncrement(&calls) == 1) patch_everywhere();
    translated = path ? translated_for(path) : NULL;
    result = real_create_file(translated ? translated : path, access, share, attributes, creation,
                              flags, template_file);
    if (result != INVALID_HANDLE_VALUE) track(result, path);
    inside_hook = 0;
    return result;
}

/* The port opens its archives through the C runtime, so the executable itself may import none
 * of the APIs this hook needs (sanctuary-probe.exe imports no CreateFileW: ucrtbase does). A
 * thread started from DllMain patches every loaded module once the loader lock is gone, and
 * keeps catching modules loaded later; the lazy patch on the first hooked call would never
 * start when nothing is hooked yet. */
static DWORD WINAPI installer_thread(LPVOID unused) {
    int round;
    (void)unused;
    for (round = 0; round < 40; round++) {
        Sleep(round == 0 ? 20 : 250);
        patch_everywhere();
    }
    return 0;
}

/* ---------------------------------------------------------------- install */

void hu_install_hooks(HINSTANCE self) {
    WCHAR dll[MAX_PATH];
    DWORD length = GetModuleFileNameW(self, dll, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) return;
    while (length > 0 && dll[length] != L'\\') length--;
    if (dll[length] != L'\\') return;
    dll[length + 1] = 0;
    lstrcpynW(module_dir, dll, MAX_PATH);
    lstrcpynW(data_dir, module_dir, MAX_PATH);
    lstrcatW(data_dir, HU_DATA_DIR);
    lstrcatW(data_dir, L"\\");

    real_create_file = (create_file_t)(void (*)(void))GetProcAddress(
        GetModuleHandleW(L"kernel32.dll"), "CreateFileW");
    real_read_file = (read_file_t)(void (*)(void))GetProcAddress(
        GetModuleHandleW(L"kernel32.dll"), "ReadFile");
    real_set_pointer = (set_pointer_t)(void (*)(void))GetProcAddress(
        GetModuleHandleW(L"kernel32.dll"), "SetFilePointerEx");
    if (!real_create_file || !real_read_file || !real_set_pointer) return;
    /* Only the executable here: inside DllMain the loader lock makes a module snapshot
       unreliable, and the executable is the module whose calls matter first. */
    patch_module(GetModuleHandleW(NULL), "CreateFileW", (void *)&hook_create_file);
    CloseHandle(CreateThread(NULL, 0, installer_thread, NULL, 0, NULL));
}
