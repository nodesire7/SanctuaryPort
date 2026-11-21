/* Test host: proves the CPK hook serves rewritten bytes without touching the file.
 *
 * Loads the proxy DLL, opens the credit archive, seeks to the sector that holds the entry and
 * reads it, writing what the process saw to a file. A plain read of the same range keeps the
 * archive's own bytes, so the two can be compared.
 *
 * usage: cpk_test.exe PORT_DIR ARCHIVE OFFSET OUT
 */
#include <windows.h>
#include <stdio.h>

int wmain(int argc, wchar_t **argv) {
    WCHAR dll[MAX_PATH];
    HANDLE file;
    LARGE_INTEGER where;
    unsigned char *buffer;
    DWORD got = 0, wrote = 0;
    HANDLE out;
    if (argc < 5) {
        wprintf(L"usage: cpk_test <port dir> <archive> <offset> <out>\n");
        return 2;
    }
    wsprintfW(dll, L"%s\\bin\\SDL3.dll", argv[1]);
    if (!LoadLibraryW(dll)) {
        wprintf(L"proxy load failed: %lu\n", GetLastError());
        return 3;
    }
    file = CreateFileW(argv[2], GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        wprintf(L"archive open failed: %lu\n", GetLastError());
        return 4;
    }
    where.QuadPart = _wtoi64(argv[3]);
    if (!SetFilePointerEx(file, where, NULL, FILE_BEGIN)) {
        wprintf(L"seek failed: %lu\n", GetLastError());
        return 5;
    }
    buffer = HeapAlloc(GetProcessHeap(), 0, 16384);
    if (!buffer || !ReadFile(file, buffer, 16384, &got, NULL)) {
        wprintf(L"read failed: %lu\n", GetLastError());
        return 6;
    }
    CloseHandle(file);
    out = CreateFileW(argv[4], GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (out == INVALID_HANDLE_VALUE) return 7;
    WriteFile(out, buffer, got, &wrote, NULL);
    CloseHandle(out);
    wprintf(L"read %lu bytes\n", got);
    return 0;
}
