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

#define HU_DATA_DIR L"zh-CN"

typedef HANDLE(WINAPI *create_file_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD,
                                      DWORD, HANDLE);

static create_file_t real_create_file;

static WCHAR module_dir[MAX_PATH]; /* ...\bin\ */
static WCHAR data_dir[MAX_PATH];   /* ...\bin\zh-CN\ */
static LONG calls;
static __thread int inside_hook;

static HANDLE WINAPI hook_create_file(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD,
                                      HANDLE);
static void patch_everywhere(void);

/* ---------------------------------------------------------------- helpers */

static BOOL ends_with_ci(const WCHAR *text, const WCHAR *suffix) {
    size_t have = lstrlenW(text), want = lstrlenW(suffix);
    if (want > have) return FALSE;
    return lstrcmpiW(text + (have - want), suffix) == 0;
}

/* The translated copy of `path` when this package ships one, else NULL. */
static const WCHAR *translated_for(const WCHAR *path) {
    static WCHAR candidate[MAX_PATH];
    const WCHAR *name;

    if (ends_with_ci(path, L"\\assets\\i18n.json")) {
        name = L"i18n.json";
    } else if (ends_with_ci(path, L"\\assets\\trophies\\Diablo_III.json")) {
        name = L"trophies.json";
    } else if (ends_with_ci(path, L"\\changeinfo.xml")) {
        name = L"changeinfo.xml";
    } else {
        return NULL;
    }
    lstrcpyW(candidate, data_dir);
    lstrcatW(candidate, name);
    return GetFileAttributesW(candidate) == INVALID_FILE_ATTRIBUTES ? NULL : candidate;
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
    inside_hook = 0;
    return result;
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
    if (!real_create_file) return;
    /* Only the executable here: inside DllMain the loader lock makes a module snapshot
       unreliable, and the executable is the module whose calls matter first. */
    patch_module(GetModuleHandleW(NULL), "CreateFileW", (void *)&hook_create_file);
}
