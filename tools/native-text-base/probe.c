/* Native library checks only; this executable does not start the game. */
#include <errno.h>
#include <iconv.h>
#include <localcharset.h>
#include <pixman.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error This probe must target Android x86_64 with minimum API 23.
#endif

static int fail(const char *message)
{
    fprintf(stderr, "FAIL %s\n", message);
    return 1;
}

static int convert(const char *to, const char *from,
                   const unsigned char *input, size_t input_size,
                   const unsigned char *expected, size_t expected_size)
{
    unsigned char output[64];
    char *in = (char *)input;
    char *out = (char *)output;
    size_t in_left = input_size, out_left = sizeof(output);
    iconv_t cd = iconv_open(to, from);
    if (cd == (iconv_t)-1) return fail("iconv_open for conversion");
    memset(output, 0xcc, sizeof(output));
    size_t result = iconv(cd, &in, &in_left, &out, &out_left);
    int matched = result == 0 && in_left == 0 &&
        in == (char *)input + input_size &&
        sizeof(output) - out_left == expected_size &&
        memcmp(output, expected, expected_size) == 0;
    int closed = iconv_close(cd) == 0;
    return matched && closed ? 0 : fail("iconv conversion bytes or consumed input");
}

static int conversion_checks(void)
{
    /* A, e-acute, Arabic ain, and U+1F600 (a UTF-16 surrogate pair). */
    static const unsigned char utf8[] = {0x41, 0xc3, 0xa9, 0xd8, 0xb9, 0xf0, 0x9f, 0x98, 0x80};
    static const unsigned char utf16le[] = {0x41, 0x00, 0xe9, 0x00, 0x39, 0x06, 0x3d, 0xd8, 0x00, 0xde};
    if (convert("UTF-16LE", "UTF-8", utf8, sizeof(utf8), utf16le, sizeof(utf16le)) ||
        convert("UTF-8", "UTF-16LE", utf16le, sizeof(utf16le), utf8, sizeof(utf8))) return 1;
    const char *charset = locale_charset();
    if (!charset || !charset[0]) return fail("libcharset returned an empty encoding name");
    printf("libcharset reports: %s\n", charset);
    puts("PASS libiconv conversion");
    return 0;
}

static int expect_error(const unsigned char *input, size_t input_size,
                        size_t output_size, int expected_errno)
{
    unsigned char output[8];
    char *in = (char *)input, *out = (char *)output;
    size_t in_left = input_size, out_left = output_size;
    iconv_t cd = iconv_open("UTF-16LE", "UTF-8");
    if (cd == (iconv_t)-1) return fail("iconv_open for error check");
    memset(output, 0xcc, sizeof(output));
    errno = 0;
    size_t result = iconv(cd, &in, &in_left, &out, &out_left);
    int saved_errno = errno;
    int matched = result == (size_t)-1 && saved_errno == expected_errno &&
        in_left == input_size && in == (char *)input &&
        out_left == output_size && out == (char *)output && output[0] == 0xcc;
    int closed = iconv_close(cd) == 0;
    return matched && closed ? 0 : fail("iconv error code or partial-input handling");
}

static int error_checks(void)
{
    static const unsigned char invalid[] = {0xff};
    static const unsigned char incomplete[] = {0xe2, 0x82};
    static const unsigned char multibyte[] = {0xc3, 0xa9};
    if (expect_error(invalid, sizeof(invalid), 8, EILSEQ) ||
        expect_error(incomplete, sizeof(incomplete), 8, EINVAL) ||
        expect_error(multibyte, sizeof(multibyte), 1, E2BIG)) return 1;
    errno = 0;
    iconv_t cd = iconv_open("WESNOTH-NONEXISTENT-ENCODING", "UTF-8");
    if (cd != (iconv_t)-1) {
        iconv_close(cd);
        return fail("iconv accepted an unknown encoding");
    }
    if (errno != EINVAL) return fail("iconv unknown-encoding error");
    puts("PASS libiconv error handling");
    return 0;
}

static int compositing_checks(void)
{
    /* Pixman uses premultiplied ARGB; half-red over opaque blue is exact. */
    uint32_t source[] = {0x80800000u, 0xff00ff00u, 0x00000000u, 0xffffffffu};
    uint32_t destination[] = {0xff0000ffu, 0xff0000ffu, 0xff0000ffu, 0xff0000ffu};
    const uint32_t expected[] = {0xff80007fu, 0xff00ff00u, 0xff0000ffu, 0xffffffffu};
    const uint32_t clipped[] = {0xff112233u, 0xff00ff00u, 0xff112233u, 0xffffffffu};
    pixman_image_t *src = pixman_image_create_bits(PIXMAN_a8r8g8b8, 2, 2, source, 8);
    pixman_image_t *dst = pixman_image_create_bits(PIXMAN_a8r8g8b8, 2, 2, destination, 8);
    if (!src || !dst) {
        if (src) pixman_image_unref(src);
        if (dst) pixman_image_unref(dst);
        return fail("Pixman image allocation");
    }
    pixman_image_composite32(PIXMAN_OP_OVER, src, NULL, dst, 0, 0, 0, 0, 0, 0, 2, 2);
    int matched = memcmp(destination, expected, sizeof(expected)) == 0;
    for (size_t i = 0; i < 4; ++i) destination[i] = 0xff112233u;
    pixman_region32_t clip;
    pixman_region32_init_rect(&clip, 1, 0, 1, 2);
    matched = pixman_image_set_clip_region32(dst, &clip) && matched;
    pixman_image_composite32(PIXMAN_OP_SRC, src, NULL, dst, 0, 0, 0, 0, 0, 0, 2, 2);
    matched = memcmp(destination, clipped, sizeof(clipped)) == 0 && matched;
    pixman_region32_fini(&clip);
    pixman_image_unref(src);
    pixman_image_unref(dst);
    if (!matched) return fail("Pixman alpha compositing or destination clipping");
    puts("PASS Pixman compositing");
    return 0;
}

int main(void)
{
    puts("WESNOTH_ANDROID_TEXT_BASE version=1.19.26 abi=x86_64 min_api=23");
    printf("Text base libraries: libiconv %d.%d; Pixman %s\n",
           _libiconv_version >> 8, _libiconv_version & 255, pixman_version_string());
    if (_LIBICONV_VERSION != 0x0111 || _libiconv_version != 0x0111 ||
        PIXMAN_VERSION != 4202 || pixman_version() != 4202 ||
        strcmp(pixman_version_string(), "0.42.2") != 0)
        return fail("library versions do not match the pinned releases");
    if (conversion_checks() || error_checks() || compositing_checks()) return 1;
    puts("PASS wesnoth-android-text-base");
    return 0;
}
