/* Native prerequisites only; this executable does not start GLib or the game. */
#define PCRE2_CODE_UNIT_WIDTH 8
#include <ffi.h>
#include <libintl.h>
#include <locale.h>
#include <pcre2.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error This probe must target Android x86_64 with minimum API 23.
#endif

static int fail(const char *message)
{
    fprintf(stderr, "FAIL %s\n", message);
    return 1;
}

static int catalog_checks(const char *directory)
{
    const char *domain = "wesnoth-glib-deps";
    /* The fixture contains ISO-8859-1 bytes, so this also exercises iconv. */
    if (setenv("LANGUAGE", "fr", 1) || setenv("LC_ALL", "C.UTF-8", 1))
        return fail("setting catalog locale");
    const char *locale = setlocale(LC_ALL, "C.UTF-8");
    if (!locale || !strcmp(locale, "C") || !strcmp(locale, "POSIX"))
        return fail("C.UTF-8 locale unavailable");
    printf("Catalog locale: %s\n", locale);
    if (!bindtextdomain(domain, directory) || !bind_textdomain_codeset(domain, "UTF-8"))
        return fail("binding catalog directory or output encoding");
    if (strcmp(dgettext(domain, "Ready"), "Pr\xc3\xaat") ||
        strcmp(dngettext(domain, "%d unit", "%d units", 1), "%d soldat") ||
        strcmp(dngettext(domain, "%d unit", "%d units", 2), "%d soldats") ||
        strcmp(dgettext(domain, "Missing message"), "Missing message"))
        return fail("catalog translation, encoding, plural selection or fallback");
    puts("PASS gettext catalog");
    return 0;
}

static int regex_checks(void)
{
    int error;
    PCRE2_SIZE offset;
    const char *pattern = "^(?<name>\\p{L}+)-(?<count>[0-9]+)$";
    /* Arabic letters (ain, mim), then a decimal count; seven UTF-8 bytes total. */
    const unsigned char subject[] = {0xd8, 0xb9, 0xd9, 0x85, '-', '2', '3', 0};
    pcre2_code *code = pcre2_compile((PCRE2_SPTR)pattern, PCRE2_ZERO_TERMINATED,
                                    PCRE2_UTF | PCRE2_UCP, &error, &offset, NULL);
    if (!code) return fail("PCRE2 Unicode pattern compilation");
    pcre2_match_data *match = pcre2_match_data_create_from_pattern(code, NULL);
    if (!match) { pcre2_code_free(code); return fail("PCRE2 match allocation"); }
    int result = pcre2_match(code, subject, sizeof(subject) - 1, 0, 0, match, NULL);
    int name = pcre2_substring_number_from_name(code, (PCRE2_SPTR)"name");
    int count = pcre2_substring_number_from_name(code, (PCRE2_SPTR)"count");
    PCRE2_SIZE *spans = pcre2_get_ovector_pointer(match);
    int good = result == 3 && name == 1 && count == 2 &&
        spans[0] == 0 && spans[1] == 7 && spans[2] == 0 && spans[3] == 4 &&
        spans[4] == 5 && spans[5] == 7;
    result = pcre2_match(code, (PCRE2_SPTR)"123-23", 6, 0, 0, match, NULL);
    good = good && result == PCRE2_ERROR_NOMATCH;
    const unsigned char invalid[] = {0xff, '-', '2', '3'};
    result = pcre2_match(code, invalid, sizeof(invalid), 0, 0, match, NULL);
    good = good && result <= PCRE2_ERROR_UTF8_ERR1 && result >= PCRE2_ERROR_UTF8_ERR21;
    pcre2_match_data_free(match);
    pcre2_code_free(code);
    code = pcre2_compile((PCRE2_SPTR)"(", 1, PCRE2_UTF, &error, &offset, NULL);
    if (code) { pcre2_code_free(code); return fail("PCRE2 accepted malformed pattern"); }
    if (!good || error <= 0) return fail("PCRE2 Unicode captures or invalid-input handling");
    puts("PASS PCRE2 Unicode");
    return 0;
}

typedef struct { int32_t count; double weight; } unit_value;

static unit_value make_value(int32_t count, double weight)
{
    unit_value value = {count + 5, weight * 1.5};
    return value;
}

static double mix_value(int32_t count, double weight)
{
    return count * weight + 0.25;
}

static int ffi_call_checks(void)
{
    ffi_type *arguments[] = {&ffi_type_sint32, &ffi_type_double};
    int32_t count = 7;
    double weight = 2.0;
    void *values[] = {&count, &weight};
    ffi_cif cif;
    double scalar = 0;
    if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_double, arguments) != FFI_OK)
        return fail("libffi scalar call preparation");
    ffi_call(&cif, FFI_FN(mix_value), &scalar, values);
    if (scalar != 14.25) return fail("libffi mixed integer/double call");
    ffi_type *members[] = {&ffi_type_sint32, &ffi_type_double, NULL};
    ffi_type structure = {0, 0, FFI_TYPE_STRUCT, members};
    unit_value result = {0, 0};
    if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &structure, arguments) != FFI_OK ||
        structure.size != sizeof(unit_value) || structure.alignment != _Alignof(unit_value))
        return fail("libffi structure ABI layout");
    ffi_call(&cif, FFI_FN(make_value), &result, values);
    if (result.count != 12 || result.weight != 3.0) return fail("libffi structure return");
    puts("PASS libffi calls");
    return 0;
}

#if FFI_CLOSURES
static void closure_callback(ffi_cif *cif, void *result, void **arguments, void *user_data)
{
    (void)cif;
    *(double *)result = *(int32_t *)arguments[0] * *(double *)arguments[1] + *(double *)user_data;
}
#endif

static int ffi_closure_checks(void)
{
#if FFI_CLOSURES
    ffi_cif cif;
    ffi_type *arguments[] = {&ffi_type_sint32, &ffi_type_double};
    void *code = NULL;
    double offset = 0.5;
    if (ffi_prep_cif(&cif, FFI_DEFAULT_ABI, 2, &ffi_type_double, arguments) != FFI_OK)
        return fail("libffi closure call preparation");
    ffi_closure *closure = ffi_closure_alloc(sizeof(*closure), &code);
    if (!closure || !code) {
        if (closure) ffi_closure_free(closure);
        return fail("libffi executable closure allocation");
    }
    if (ffi_prep_closure_loc(closure, &cif, closure_callback, &offset, code) != FFI_OK) {
        ffi_closure_free(closure);
        return fail("libffi closure preparation");
    }
    double (*callback)(int32_t, double);
    _Static_assert(sizeof(callback) == sizeof(code), "Unexpected native function pointer size");
    memcpy(&callback, &code, sizeof(callback));
    double result = callback(4, 3.0);
    ffi_closure_free(closure);
    if (result != 12.5) return fail("libffi callback arguments/result/user data");
    puts("PASS libffi closure");
    return 0;
#else
    return fail("libffi closure support disabled");
#endif
}

int main(int argc, char **argv)
{
    puts("WESNOTH_ANDROID_GLIB_DEPS version=1.19.26 abi=x86_64 min_api=23");
    if (argc != 2 || argv[1][0] != '/') return fail("expected absolute catalog directory argument");
    char version[64] = {0};
    uint32_t unicode = 0;
    if (LIBINTL_VERSION != 0x001501 || libintl_version != 0x001501 ||
        PCRE2_MAJOR != 10 || PCRE2_MINOR != 42 ||
        pcre2_config(PCRE2_CONFIG_VERSION, version) <= 0 || strncmp(version, "10.42 ", 6) ||
        pcre2_config(PCRE2_CONFIG_UNICODE, &unicode) != 0 || unicode != 1)
        return fail("pinned library versions or Unicode support differ");
    printf("Libraries: gettext 0.21.1; PCRE2 %s; libffi source pin 3.4.4\n", version);
    if (catalog_checks(argv[1]) || regex_checks() || ffi_call_checks() || ffi_closure_checks()) return 1;
    puts("PASS wesnoth-android-glib-deps");
    return 0;
}
