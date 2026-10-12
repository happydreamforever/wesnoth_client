#include <expat.h>
#include <fontconfig/fontconfig.h>
#include <cairo.h>
#include <cairo-ft.h>
#include <ft2build.h>
#include FT_FREETYPE_H
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error "Build the selected Android x86_64/API23 probe."
#endif

static int fail(const char *message)
{
    fprintf(stderr, "FAIL %s\n", message);
    return 0;
}

typedef struct {
    int starts, ends, invalid, external_calls;
    size_t used;
    char text[80];
} XmlState;

static void XMLCALL element_start(void *opaque, const XML_Char *name,
                                  const XML_Char **attributes)
{
    XmlState *state = opaque;
    const char *expected = state->starts == 0 ? "urn:wesnoth|root" : "urn:wesnoth|label";
    if (state->starts > 1 || strcmp(name, expected) || attributes[0])
        state->invalid = 1;
    ++state->starts;
}

static void XMLCALL element_end(void *opaque, const XML_Char *name)
{
    XmlState *state = opaque;
    const char *expected = state->ends == 0 ? "urn:wesnoth|label" : "urn:wesnoth|root";
    if (state->ends > 1 || strcmp(name, expected))
        state->invalid = 1;
    ++state->ends;
}

static void XMLCALL characters(void *opaque, const XML_Char *text, int length)
{
    XmlState *state = opaque;
    if (length < 0 || (size_t)length >= sizeof(state->text) - state->used) {
        state->invalid = 1;
        return;
    }
    memcpy(state->text + state->used, text, (size_t)length);
    state->used += (size_t)length;
    state->text[state->used] = '\0';
}

static int XMLCALL reject_external(XML_Parser parser, const XML_Char *context,
                                   const XML_Char *base, const XML_Char *system_id,
                                   const XML_Char *public_id)
{
    (void)context; (void)base; (void)system_id; (void)public_id;
    XmlState *state = XML_GetUserData(parser);
    ++state->external_calls;
    return XML_STATUS_ERROR;
}

static int test_xml(void)
{
    static const char document[] = "<w:root xmlns:w=\"urn:wesnoth\"><w:label>Wesnoth &amp; \xd8\xb9\xd9\x85</w:label></w:root>";
    static const char bad[] = "<root><child></root>";
    static const char external[] = "<!DOCTYPE root [<!ENTITY ext SYSTEM \"urn:wesnoth:blocked\">]><root>&ext;</root>";
    XmlState state = {0};
    XML_Parser parser = XML_ParserCreateNS("UTF-8", '|');
    if (!parser) return fail("Expat parser allocation");
    XML_SetUserData(parser, &state);
    XML_SetElementHandler(parser, element_start, element_end);
    XML_SetCharacterDataHandler(parser, characters);
    XML_SetExternalEntityRefHandler(parser, reject_external);
    int good = XML_SetParamEntityParsing(parser, XML_PARAM_ENTITY_PARSING_NEVER)
        && XML_Parse(parser, document, 17, XML_FALSE) == XML_STATUS_OK
        && XML_Parse(parser, document + 17, (int)sizeof(document) - 18, XML_TRUE) == XML_STATUS_OK
        && !state.invalid && state.starts == 2 && state.ends == 2
        && state.external_calls == 0 && !strcmp(state.text, "Wesnoth & \xd8\xb9\xd9\x85");
    XML_ParserFree(parser);
    if (!good) return fail("Expat incremental Unicode/namespace parsing");
    parser = XML_ParserCreate(NULL);
    if (!parser) return fail("Expat negative parser allocation");
    good = XML_Parse(parser, bad, (int)sizeof(bad) - 1, XML_TRUE) == XML_STATUS_ERROR
        && XML_GetErrorCode(parser) == XML_ERROR_TAG_MISMATCH;
    XML_ParserFree(parser);
    if (!good) return fail("Expat mismatched-tag rejection");
    parser = XML_ParserCreate(NULL);
    if (!parser) return fail("Expat external-entity parser allocation");
    XML_SetUserData(parser, &state);
    XML_SetExternalEntityRefHandler(parser, reject_external);
    good = XML_Parse(parser, external, (int)sizeof(external) - 1, XML_TRUE) == XML_STATUS_ERROR
        && XML_GetErrorCode(parser) == XML_ERROR_EXTERNAL_ENTITY_HANDLING
        && state.external_calls == 1;
    XML_ParserFree(parser);
    if (!good) return fail("Expat external-entity handler rejection");
    puts("PASS Expat XML and errors");
    return 1;
}

static FcPattern *match_font(FcConfig *config, FcChar32 character)
{
    FcPattern *request = FcPatternCreate();
    FcCharSet *charset = FcCharSetCreate();
    FcPattern *match = NULL;
    FcResult result = FcResultNoMatch;
    if (request && charset && FcCharSetAddChar(charset, character)
        && FcPatternAddString(request, FC_FAMILY, (const FcChar8 *)"WesnothProbe")
        && FcPatternAddCharSet(request, FC_CHARSET, charset)
        && FcPatternAddDouble(request, FC_PIXEL_SIZE, 32.0)
        && FcConfigSubstitute(config, request, FcMatchPattern)) {
        FcConfigSetDefaultSubstitute(config, request);
        match = FcFontMatch(config, request, &result);
        if (result != FcResultMatch) {
            if (match) FcPatternDestroy(match);
            match = NULL;
        }
    }
    if (charset) FcCharSetDestroy(charset);
    if (request) FcPatternDestroy(request);
    return match;
}

static int check_match(FcPattern *pattern, const char *file,
                       const char *family, FcChar32 character)
{
    FcChar8 *selected_file = NULL, *selected_family = NULL;
    FcCharSet *charset = NULL;
    int index = -1;
    return pattern
        && FcPatternGetString(pattern, FC_FILE, 0, &selected_file) == FcResultMatch
        && FcPatternGetString(pattern, FC_FAMILY, 0, &selected_family) == FcResultMatch
        && FcPatternGetCharSet(pattern, FC_CHARSET, 0, &charset) == FcResultMatch
        && FcPatternGetInteger(pattern, FC_INDEX, 0, &index) == FcResultMatch
        && index == 0 && !strcmp((const char *)selected_file, file)
        && !strcmp((const char *)selected_family, family)
        && FcCharSetHasChar(charset, character);
}

static uint32_t pixel(cairo_surface_t *surface, int x, int y)
{
    uint32_t value;
    memcpy(&value, cairo_image_surface_get_data(surface)
           + y * cairo_image_surface_get_stride(surface) + x * 4, sizeof(value));
    return value;
}

static int test_image(void)
{
    cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 16, 16);
    cairo_t *cr = cairo_create(surface);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 1, 0, 0, 0.5);
    cairo_rectangle(cr, 0, 0, 8, 8);
    cairo_fill(cr);
    cairo_set_operator(cr, CAIRO_OPERATOR_OVER);
    cairo_set_source_rgba(cr, 0, 0, 1, 0.5);
    cairo_rectangle(cr, 0, 0, 4, 4);
    cairo_fill(cr);
    cairo_rectangle(cr, 8, 8, 8, 8);
    cairo_clip(cr);
    cairo_set_source_rgb(cr, 0, 1, 0);
    cairo_paint(cr);
    cairo_surface_flush(surface);
    uint32_t mixed = pixel(surface, 1, 1), red = pixel(surface, 6, 6);
    unsigned alpha = mixed >> 24, r = (mixed >> 16) & 255, b = mixed & 255;
    int good = cairo_status(cr) == CAIRO_STATUS_SUCCESS
        && cairo_surface_status(surface) == CAIRO_STATUS_SUCCESS
        && alpha >= 190 && alpha <= 192 && r >= 63 && r <= 65
        && b >= 127 && b <= 128 && (mixed & 0xff00) == 0
        && (red >> 24) >= 127 && (red >> 24) <= 128
        && ((red >> 16) & 255) == (red >> 24) && (red & 0xffff) == 0
        && pixel(surface, 12, 12) == UINT32_C(0xff00ff00)
        && pixel(surface, 12, 2) == 0 && pixel(surface, 2, 12) == 0;
    cairo_destroy(cr);
    cairo_surface_destroy(surface);
    if (!good) return fail("Cairo premultiplied alpha, OVER compositing or clip isolation");
    puts("PASS Cairo image compositing");
    return 1;
}

static int render(FcPattern *pattern, const char *text)
{
    cairo_font_face_t *face = cairo_ft_font_face_create_for_pattern(pattern);
    cairo_matrix_t scale, identity;
    cairo_matrix_init_scale(&scale, 32, 32);
    cairo_matrix_init_identity(&identity);
    cairo_font_options_t *options = cairo_font_options_create();
    cairo_font_options_set_antialias(options, CAIRO_ANTIALIAS_GRAY);
    cairo_scaled_font_t *font = cairo_scaled_font_create(face, &scale, &identity, options);
    cairo_glyph_t *glyphs = NULL;
    int count = 0;
    cairo_status_t converted = cairo_scaled_font_text_to_glyphs(font, 8, 48, text, -1,
                                                               &glyphs, &count, NULL, NULL, NULL);
    cairo_text_extents_t extents = {0};
    if (converted == CAIRO_STATUS_SUCCESS && count > 0)
        cairo_scaled_font_glyph_extents(font, glyphs, count, &extents);
    int good = cairo_font_face_status(face) == CAIRO_STATUS_SUCCESS
        && cairo_scaled_font_status(font) == CAIRO_STATUS_SUCCESS
        && converted == CAIRO_STATUS_SUCCESS && count > 0 && extents.width > 0 && extents.height > 0;
    for (int i = 0; i < count; ++i) if (glyphs[i].index == 0) good = 0;
    cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 256, 80);
    cairo_t *cr = cairo_create(surface);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_set_scaled_font(cr, font);
    if (good) cairo_show_glyphs(cr, glyphs, count);
    cairo_surface_flush(surface);
    unsigned ink = 0;
    for (int y = 0; y < 80; ++y) {
        for (int x = 0; x < 256; ++x) {
            uint32_t value = pixel(surface, x, y);
            if ((value >> 24) != 255) good = 0;
            if (value != UINT32_C(0xffffffff)) ++ink;
        }
    }
    good = good && ink > 10 && ink < 256 * 80 / 2
        && cairo_status(cr) == CAIRO_STATUS_SUCCESS
        && cairo_surface_status(surface) == CAIRO_STATUS_SUCCESS
        && pixel(surface, 0, 0) == UINT32_C(0xffffffff)
        && pixel(surface, 255, 79) == UINT32_C(0xffffffff);
    cairo_destroy(cr);
    cairo_surface_destroy(surface);
    cairo_glyph_free(glyphs);
    cairo_scaled_font_destroy(font);
    cairo_font_options_destroy(options);
    cairo_font_face_destroy(face);
    return good;
}

int main(int argc, char **argv)
{
    puts("WESNOTH_ANDROID_TEXT_RENDER version=1.19.26 abi=x86_64 min_api=23");
    XML_Expat_Version expat = XML_ExpatVersionInfo();
    FT_Library ft = NULL;
    FT_Int major = 0, minor = 0, patch = 0;
    if (FT_Init_FreeType(&ft)) return !fail("FreeType initialization");
    FT_Library_Version(ft, &major, &minor, &patch);
    FT_Done_FreeType(ft);
    printf("Expat: %d.%d.%d; Fontconfig: %d; Cairo: %s; FreeType: %d.%d.%d\n",
           expat.major, expat.minor, expat.micro, FcGetVersion(), cairo_version_string(), major, minor, patch);
    if (argc != 3 || expat.major != 2 || expat.minor != 5 || expat.micro != 0
        || XML_MAJOR_VERSION != 2 || XML_MINOR_VERSION != 5 || XML_MICRO_VERSION != 0
        || FC_VERSION != 21701 || FcGetVersion() != 21701
        || CAIRO_VERSION != CAIRO_VERSION_ENCODE(1, 16, 0) || cairo_version() != CAIRO_VERSION
        || major != 2 || minor != 13 || patch != 0)
        return !fail("Expected two font paths and pinned library versions");
    if (!test_xml()) return 1;
    static const FcChar8 config_xml[] =
        "<fontconfig><alias><family>WesnothProbe</family><prefer><family>Lato</family>"
        "<family>DejaVu Sans</family></prefer></alias></fontconfig>";
    FcConfig *config = FcConfigCreate();
    if (!config || !FcConfigParseAndLoadFromMemory(config, config_xml, FcTrue)
        || !FcConfigAppFontAddFile(config, (const FcChar8 *)argv[1])
        || !FcConfigAppFontAddFile(config, (const FcChar8 *)argv[2]))
        return !fail("Fontconfig private configuration or font registration");
    FcFontSet *app_fonts = FcConfigGetFonts(config, FcSetApplication);
    FcFontSet *system_fonts = FcConfigGetFonts(config, FcSetSystem);
    FcPattern *latin = match_font(config, 'W');
    FcPattern *arabic = match_font(config, 0x0639);
    FcCharSet *latin_charset = NULL;
    if (!app_fonts || app_fonts->nfont != 2 || (system_fonts && system_fonts->nfont)
        || !check_match(latin, argv[1], "Lato", 'W'))
        return !fail("Fontconfig explicit Lato selection or system-font isolation");
    puts("PASS Fontconfig private font selection");
    if (!check_match(arabic, argv[2], "DejaVu Sans", 0x0639)
        || FcPatternGetCharSet(latin, FC_CHARSET, 0, &latin_charset) != FcResultMatch
        || FcCharSetHasChar(latin_charset, 0x0639))
        return !fail("Fontconfig character coverage fallback to DejaVu Sans");
    puts("PASS Fontconfig character fallback");
    if (!test_image()) return 1;
    if (!render(latin, "Wesnoth") || !render(arabic, "\xd8\xb9"))
        return !fail("Cairo rendering of selected Latin and Arabic glyphs");
    puts("PASS Cairo FreeType glyph rendering");
    FcPatternDestroy(latin);
    FcPatternDestroy(arabic);
    cairo_debug_reset_static_data();
    FcConfigDestroy(config);
    puts("PASS wesnoth-android-text-render");
    return 0;
}
