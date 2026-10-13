#include <unicode/ubrk.h>
#include <unicode/uclean.h>
#include <unicode/ucol.h>
#include <unicode/udata.h>
#include <unicode/udat.h>
#include <unicode/uchar.h>
#include <unicode/normlzr.h>
#include <unicode/unum.h>
#include <unicode/uregex.h>
#include <unicode/ustring.h>
#include <unicode/uversion.h>

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>

#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error "Build the selected Android x86_64/API23 probe."
#endif
#if U_ICU_VERSION_MAJOR_NUM != 78 || U_ICU_VERSION_MINOR_NUM != 3 || U_ICU_VERSION_PATCHLEVEL_NUM != 0
#error "This probe requires the pinned ICU 78.3 headers."
#endif

extern "C" const char U_ICUDATA_ENTRY_POINT[];

static bool fail(const char* message)
{
    std::fprintf(stderr, "FAIL %s\n", message);
    return false;
}

template<typename T, void (*Close)(T*)>
using Handle = std::unique_ptr<T, decltype(Close)>;

static bool equal(const UChar* actual, int32_t length, const UChar* expected)
{
    return length == u_strlen(expected) && u_memcmp(actual, expected, length) == 0;
}

static bool packaged_data()
{
    UErrorCode status = U_ZERO_ERROR;
    // This standalone process registers its linked data before any ICU use.
    // NO_FILES forbids disk data loading even if an ambient ICU_DATA is present.
    udata_setFileAccess(UDATA_NO_FILES, &status);
    u_setDataDirectory("/wesnoth-icu-probe-no-external-data");
    if (status != U_ZERO_ERROR
        || (reinterpret_cast<std::uintptr_t>(U_ICUDATA_ENTRY_POINT) & 15) != 0)
        return fail("ICU embedded data access mode or alignment");
    udata_setCommonData(U_ICUDATA_ENTRY_POINT, &status);
    if (status != U_ZERO_ERROR) return fail("ICU embedded data registration");
    u_init(&status);
    if (status != U_ZERO_ERROR) return fail("ICU initialization from embedded data");
    UVersionInfo library{}, unicode{};
    static const uint8_t expected_library[] = {78, 3, 0, 0};
    static const uint8_t expected_unicode[] = {17, 0, 0, 0};
    u_getVersion(library);
    u_getUnicodeVersion(unicode);
    if (std::memcmp(library, expected_library, sizeof(library))
        || std::memcmp(unicode, expected_unicode, sizeof(unicode)))
        return fail("ICU runtime or Unicode version");
    char library_text[U_MAX_VERSION_STRING_LENGTH]{};
    char unicode_text[U_MAX_VERSION_STRING_LENGTH]{};
    u_versionToString(library, library_text);
    u_versionToString(unicode, unicode_text);
    std::printf("ICU=%s Unicode=%s; embedded data; external data files disabled\n",
                library_text, unicode_text);
    std::puts("PASS ICU packaged data and versions");
    return true;
}

static bool normalization_and_utf()
{
    UErrorCode status = U_ZERO_ERROR;
    // Boost.Locale uses this retained C++ API; do not hide deprecated APIs.
    icu::UnicodeString normalized;
    icu::Normalizer::normalize(icu::UnicodeString(u"Cafe\u0301"), UNORM_NFC, 0,
                              normalized, status);
    if (U_FAILURE(status) || normalized != icu::UnicodeString(u"Caf\u00e9"))
        return fail("ICU NFC normalization through the C++ Normalizer API");

    UChar folded[64]{};
    int32_t length = u_strFoldCase(folded, 64, u"Stra\u00dfe \u0130\u03a3\u03c2", -1,
                                   U_FOLD_CASE_DEFAULT, &status);
    if (U_FAILURE(status) || !equal(folded, length, u"strasse i\u0307\u03c3\u03c3"))
        return fail("ICU Unicode case folding and expansion");

    static const char utf8[] = "Wesnoth caf\xc3\xa9 \xf0\x9f\x98\x80";
    static const UChar utf16[] = u"Wesnoth caf\u00e9 \U0001f600";
    UChar decoded[64]{};
    u_strFromUTF8(decoded, 64, &length, utf8, sizeof(utf8) - 1, &status);
    if (U_FAILURE(status) || !equal(decoded, length, utf16))
        return fail("ICU UTF-8 to UTF-16 supplementary character conversion");
    char encoded[64]{};
    int32_t bytes = 0;
    u_strToUTF8(encoded, 64, &bytes, decoded, length, &status);
    if (U_FAILURE(status) || bytes != sizeof(utf8) - 1
        || std::memcmp(encoded, utf8, bytes))
        return fail("ICU UTF-16 to UTF-8 round trip");

    static const char invalid_utf8[] = "\xf0\x28\x8c\x28";
    status = U_ZERO_ERROR;
    u_strFromUTF8(decoded, 64, &length, invalid_utf8, 4, &status);
    if (status != U_INVALID_CHAR_FOUND) return fail("ICU strict malformed UTF-8 rejection");
    static const UChar invalid_utf16[] = {0xd800};
    status = U_ZERO_ERROR;
    u_strToUTF8(encoded, 64, &bytes, invalid_utf16, 1, &status);
    if (status != U_INVALID_CHAR_FOUND) return fail("ICU strict unpaired surrogate rejection");
    std::puts("PASS ICU normalization casefold and strict UTF");
    return true;
}

static bool collation_and_formatting()
{
    UErrorCode status = U_ZERO_ERROR;
    Handle<UCollator, ucol_close> german(ucol_open("de_DE", &status), ucol_close);
    if (U_FAILURE(status) || !german) return fail("ICU German collation data");
    status = U_ZERO_ERROR;
    Handle<UCollator, ucol_close> swedish(ucol_open("sv_SE", &status), ucol_close);
    if (U_FAILURE(status) || !swedish) return fail("ICU Swedish collation data");
    if (ucol_strcoll(german.get(), u"\u00e4", 1, u"z", 1) != UCOL_LESS
        || ucol_strcoll(swedish.get(), u"\u00e4", 1, u"z", 1) != UCOL_GREATER
        || ucol_strcoll(german.get(), u"\u00e4", 1, u"a\u0308", 2) != UCOL_EQUAL)
        return fail("ICU locale-specific collation order or canonical equivalence");

    status = U_ZERO_ERROR;
    Handle<UNumberFormat, unum_close> number(
        unum_open(UNUM_DECIMAL, nullptr, 0, "de_DE", nullptr, &status), unum_close);
    if (U_FAILURE(status) || !number) return fail("ICU German number format data");
    unum_setAttribute(number.get(), UNUM_MIN_FRACTION_DIGITS, 2);
    unum_setAttribute(number.get(), UNUM_MAX_FRACTION_DIGITS, 2);
    UChar output[64]{};
    int32_t length = unum_formatDouble(number.get(), 12345.5, output, 64, nullptr, &status);
    if (U_FAILURE(status) || !equal(output, length, u"12.345,50"))
        return fail("ICU locale-specific number formatting");

    status = U_ZERO_ERROR;
    Handle<UDateFormat, udat_close> date(
        udat_open(UDAT_PATTERN, UDAT_PATTERN, "en_US_POSIX", u"Etc/UTC", -1,
                  u"yyyy-MM-dd HH:mm", -1, &status), udat_close);
    if (U_FAILURE(status) || !date) return fail("ICU explicit UTC date formatter");
    length = udat_format(date.get(), 0.0, output, 64, nullptr, &status);
    if (U_FAILURE(status) || !equal(output, length, u"1970-01-01 00:00"))
        return fail("ICU fixed timestamp and timezone formatting");
    std::puts("PASS ICU locale collation and formatting");
    return true;
}

static bool boundaries_and_regex()
{
    static const UChar text[] = u"A\u0301\U0001f600\U0001f1fa\U0001f1f8B";
    static const int32_t expected[] = {0, 2, 4, 8, 9};
    UErrorCode status = U_ZERO_ERROR;
    Handle<UBreakIterator, ubrk_close> iterator(
        ubrk_open(UBRK_CHARACTER, "en_US", text, -1, &status), ubrk_close);
    if (U_FAILURE(status) || !iterator) return fail("ICU grapheme break data");
    int32_t boundary = ubrk_first(iterator.get());
    for (int32_t value : expected) {
        if (boundary != value) return fail("ICU grapheme boundaries for combining/emoji/flag text");
        boundary = ubrk_next(iterator.get());
    }
    if (boundary != UBRK_DONE) return fail("ICU final grapheme boundary");

    status = U_ZERO_ERROR;
    Handle<URegularExpression, uregex_close> expression(
        uregex_open(u"^(\\p{L}+)-(\\p{Nd}{2})$", -1, 0, nullptr, &status), uregex_close);
    if (U_FAILURE(status) || !expression) return fail("ICU Unicode regular expression compilation");
    uregex_setText(expression.get(), u"caf\u00e9-\u0661\u0662", -1, &status);
    if (!uregex_matches(expression.get(), 0, &status) || U_FAILURE(status))
        return fail("ICU Unicode letter/digit regular expression match");
    UChar group[32]{};
    int32_t length = uregex_group(expression.get(), 1, group, 32, &status);
    if (U_FAILURE(status) || !equal(group, length, u"caf\u00e9"))
        return fail("ICU regular expression letter capture");
    length = uregex_group(expression.get(), 2, group, 32, &status);
    if (U_FAILURE(status) || !equal(group, length, u"\u0661\u0662"))
        return fail("ICU regular expression Arabic digit capture");
    uregex_setText(expression.get(), u"caf\u00e9-XY", -1, &status);
    if (uregex_matches(expression.get(), 0, &status) || U_FAILURE(status))
        return fail("ICU regular expression non-digit rejection");
    std::puts("PASS ICU boundaries and regular expressions");
    return true;
}

int main(int argc, char**)
{
    if (argc != 1) {
        fail("ICU probe accepts no arguments or external asset paths");
        return 1;
    }
    std::puts("WESNOTH_ANDROID_ICU version=1.19.26 abi=x86_64 min_api=23");
    if (!packaged_data() || !normalization_and_utf()
        || !collation_and_formatting() || !boundaries_and_regex()) return 1;
    u_cleanup();
    std::puts("PASS wesnoth-android-icu");
    return 0;
}
