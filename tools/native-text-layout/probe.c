#include <fribidi.h>
#include <pango/pangocairo.h>
#include <pango/pangofc-fontmap.h>
#include <pango/pangofc-font.h>
#include <fontconfig/fontconfig.h>
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

static int test_bidi(void)
{
    static const FriBidiChar logical[] = {'A', ' ', 0x05d0, 0x05d1, ' ', 'B'};
    static const FriBidiChar expected[] = {'A', ' ', 0x05d1, 0x05d0, ' ', 'B'};
    static const FriBidiStrIndex expected_map[] = {0, 1, 3, 2, 4, 5};
    FriBidiChar visual[6] = {0};
    FriBidiStrIndex logical_to_visual[6], visual_to_logical[6];
    FriBidiLevel levels[6] = {0};
    FriBidiParType direction = FRIBIDI_PAR_LTR;
    if (!fribidi_log2vis(logical, 6, &direction, visual,
                        logical_to_visual, visual_to_logical, levels)
        || direction != FRIBIDI_PAR_LTR
        || memcmp(visual, expected, sizeof(expected))
        || memcmp(logical_to_visual, expected_map, sizeof(expected_map))
        || memcmp(visual_to_logical, expected_map, sizeof(expected_map)))
        return fail("FriBidi mixed-direction visual order or index mappings");
    for (int i = 0; i < 6; ++i)
        if (levels[i] != (i == 2 || i == 3 ? 1 : 0))
            return fail("FriBidi mixed-direction embedding levels");
    /* This combining character postdates the archive's stale Unicode 14 tables. */
    if (fribidi_get_bidi_type(0x1e4ec) != FRIBIDI_TYPE_NSM)
        return fail("FriBidi regenerated Unicode character table");
    puts("PASS FriBidi mixed-direction ordering");
    return 1;
}

static int selected_font(PangoFont *font, const char *file, const char *family)
{
    FcChar8 *selected_file = NULL, *selected_family = NULL;
    int index = -1;
    if (!font || !PANGO_IS_FC_FONT(font)) return 0;
    FcPattern *pattern = pango_fc_font_get_pattern(PANGO_FC_FONT(font));
    return pattern
        && FcPatternGetString(pattern, FC_FILE, 0, &selected_file) == FcResultMatch
        && FcPatternGetString(pattern, FC_FAMILY, 0, &selected_family) == FcResultMatch
        && FcPatternGetInteger(pattern, FC_INDEX, 0, &index) == FcResultMatch
        && index == 0 && !strcmp((const char *)selected_file, file)
        && !strcmp((const char *)selected_family, family);
}

static int test_shaping(PangoLayout *layout, const char *latin_file, const char *arabic_file)
{
    /* Four Arabic letters, salaam: the selected face forms a lam-alef ligature. */
    static const char text[] = "Wesnoth \xd8\xb3\xd9\x84\xd8\xa7\xd9\x85";
    const int arabic_start = 8;
    pango_layout_set_text(layout, text, -1);
    if (!g_utf8_validate(text, -1, NULL) || pango_layout_get_line_count(layout) != 1
        || pango_layout_get_unknown_glyphs_count(layout))
        return fail("Pango UTF-8 single-line layout or missing glyphs");
    PangoLayoutLine *line = pango_layout_get_line_readonly(layout, 0);
    int latin_runs = 0, arabic_runs = 0, arabic_glyphs = 0, descending_cluster = 0;
    if (!line || pango_layout_line_get_resolved_direction(line) != PANGO_DIRECTION_LTR)
        return fail("Pango paragraph base direction");
    for (GSList *node = line->runs; node; node = node->next) {
        PangoGlyphItem *run = node->data;
        PangoItem *item = run->item;
        PangoGlyphString *glyphs = run->glyphs;
        int arabic = item->offset >= arabic_start;
        if (!glyphs || glyphs->num_glyphs <= 0 || item->length <= 0
            || item->offset < 0 || item->offset + item->length > (int)strlen(text))
            return fail("Pango glyph run boundaries");
        if (arabic) {
            if (!(item->analysis.level & 1) || item->analysis.script != PANGO_SCRIPT_ARABIC
                || !selected_font(item->analysis.font, arabic_file, "DejaVu Sans"))
                return fail("Pango Arabic direction, script or DejaVu Sans fallback");
            ++arabic_runs;
            arabic_glyphs += glyphs->num_glyphs;
        } else {
            if ((item->analysis.level & 1) || item->offset + item->length > arabic_start
                || !selected_font(item->analysis.font, latin_file, "Lato"))
                return fail("Pango Latin direction or explicit Lato selection");
            ++latin_runs;
        }
        for (int i = 0; i < glyphs->num_glyphs; ++i) {
            PangoGlyph glyph = glyphs->glyphs[i].glyph;
            int cluster = glyphs->log_clusters[i];
            if (glyph == 0 || glyph == PANGO_GLYPH_EMPTY || (glyph & PANGO_GLYPH_UNKNOWN_FLAG)
                || glyphs->glyphs[i].geometry.width <= 0 || cluster < 0 || cluster >= item->length
                || (((unsigned char)text[item->offset + cluster] & 0xc0) == 0x80))
                return fail("Pango nonempty glyphs, advances or UTF-8 cluster boundaries");
            if (arabic && i > 0) {
                if (cluster > glyphs->log_clusters[i - 1])
                    return fail("Pango Arabic visual cluster order");
                if (cluster < glyphs->log_clusters[i - 1]) descending_cluster = 1;
            }
        }
    }
    if (!latin_runs || arabic_runs != 1 || arabic_glyphs != 3 || !descending_cluster)
        return fail("Pango Arabic joining/lam-alef ligature or mixed-script runs");
    puts("PASS Pango private fonts and Arabic shaping");
    return 1;
}

static int test_wrapping(PangoLayout *layout)
{
    static const char paragraph[] =
        "Wesnoth offline paragraph \xd8\xb3\xd9\x84\xd8\xa7\xd9\x85 "
        "with Latin and Arabic text, wrapped into several readable lines. "
        "Wesnoth \xd8\xb3\xd9\x84\xd8\xa7\xd9\x85 stays offline.";
    pango_layout_set_text(layout, paragraph, -1);
    pango_layout_set_width(layout, 180 * PANGO_SCALE);
    pango_layout_set_wrap(layout, PANGO_WRAP_WORD_CHAR);
    pango_layout_set_spacing(layout, 3 * PANGO_SCALE);
    int count = pango_layout_get_line_count(layout);
    PangoRectangle ink = {0}, logical = {0};
    pango_layout_get_extents(layout, &ink, &logical);
    if (!pango_layout_is_wrapped(layout) || count < 3 || count > 16
        || pango_layout_get_unknown_glyphs_count(layout)
        || ink.width <= 0 || ink.height <= 0
        || logical.width <= 0 || logical.width > 180 * PANGO_SCALE
        || logical.height <= 2 * 24 * PANGO_SCALE || logical.height > 460 * PANGO_SCALE)
        return fail("Pango wrapped paragraph dimensions or glyph coverage");
    int next_index = 0;
    for (int i = 0; i < count; ++i) {
        PangoLayoutLine *line = pango_layout_get_line_readonly(layout, i);
        if (!line || line->start_index != next_index || line->length <= 0)
            return fail("Pango wrapped line text coverage");
        pango_layout_line_get_extents(line, NULL, &logical);
        if (logical.width <= 0 || logical.width > 180 * PANGO_SCALE || logical.height <= 0)
            return fail("Pango individual line extents");
        next_index += line->length;
    }
    if (next_index != (int)strlen(paragraph))
        return fail("Pango final wrapped line boundary");
    printf("Wrapped lines: %d\n", count);
    puts("PASS Pango paragraph wrapping");
    return 1;
}

static uint32_t pixel(cairo_surface_t *surface, int x, int y)
{
    uint32_t value;
    memcpy(&value, cairo_image_surface_get_data(surface)
           + y * cairo_image_surface_get_stride(surface) + x * 4, sizeof(value));
    return value;
}

static int test_rendering(PangoLayout *layout)
{
    enum { WIDTH = 256, HEIGHT = 512, MARGIN = 16 };
    PangoRectangle ink = {0};
    pango_layout_get_pixel_extents(layout, &ink, NULL);
    if (ink.x + MARGIN < 2 || ink.y + MARGIN < 2 || ink.width <= 0 || ink.height <= 0
        || ink.x + ink.width + MARGIN >= WIDTH - 2
        || ink.y + ink.height + MARGIN >= HEIGHT - 2)
        return fail("Pango render bounds");
    cairo_surface_t *surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, WIDTH, HEIGHT);
    cairo_t *cr = cairo_create(surface);
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_move_to(cr, MARGIN, MARGIN);
    pango_cairo_show_layout(cr, layout);
    cairo_surface_flush(surface);
    int good = cairo_status(cr) == CAIRO_STATUS_SUCCESS
        && cairo_surface_status(surface) == CAIRO_STATUS_SUCCESS;
    unsigned changed = 0, gray = 0;
    if (good) {
        for (int y = 0; y < HEIGHT; ++y) {
            for (int x = 0; x < WIDTH; ++x) {
                uint32_t value = pixel(surface, x, y);
                unsigned r = (value >> 16) & 255, g = (value >> 8) & 255, b = value & 255;
                if ((value >> 24) != 255 || r != g || g != b) good = 0;
                if (value != UINT32_C(0xffffffff)) {
                    ++changed;
                    if (r > 0 && r < 255) ++gray;
                    if (x < MARGIN + ink.x - 1 || x > MARGIN + ink.x + ink.width
                        || y < MARGIN + ink.y - 1 || y > MARGIN + ink.y + ink.height)
                        good = 0;
                }
            }
        }
        good = good && changed > 100 && changed < WIDTH * HEIGHT / 2 && gray > 20
            && pixel(surface, 0, 0) == UINT32_C(0xffffffff)
            && pixel(surface, WIDTH - 1, HEIGHT - 1) == UINT32_C(0xffffffff);
    }
    cairo_destroy(cr);
    cairo_surface_destroy(surface);
    if (!good) return fail("PangoCairo glyph coverage, grayscale pixels or background isolation");
    printf("Rendered pixels: %u; antialiased pixels: %u\n", changed, gray);
    puts("PASS PangoCairo layout rendering");
    return 1;
}

int main(int argc, char **argv)
{
    puts("WESNOTH_ANDROID_TEXT_LAYOUT version=1.19.26 abi=x86_64 min_api=23");
    printf("FriBidi: %s; Unicode: %s; Pango: %s; Fontconfig: %d; Cairo: %s\n",
           FRIBIDI_VERSION, fribidi_unicode_version, pango_version_string(),
           FcGetVersion(), cairo_version_string());
    if (argc != 3 || strcmp(FRIBIDI_VERSION, "1.0.17")
        || strncmp(fribidi_version_info, "(" FRIBIDI_NAME ") 1.0.17\n",
                   strlen("(" FRIBIDI_NAME ") 1.0.17\n"))
        || strcmp(FRIBIDI_UNICODE_VERSION, "18.0.0") || strcmp(fribidi_unicode_version, "18.0.0")
        || PANGO_VERSION_MAJOR != 1 || PANGO_VERSION_MINOR != 50 || PANGO_VERSION_MICRO != 14
        || pango_version() != PANGO_VERSION_ENCODE(1, 50, 14)
        || FcGetVersion() != 21701 || cairo_version() != CAIRO_VERSION_ENCODE(1, 16, 0))
        return !fail("Expected two font paths and pinned layout library versions");
    if (!test_bidi()) return 1;

    static const FcChar8 config_xml[] =
        "<fontconfig><alias><family>WesnothLayoutProbe</family><prefer><family>Lato</family>"
        "<family>DejaVu Sans</family></prefer></alias></fontconfig>";
    FcConfig *config = FcConfigCreate();
    if (!config || !FcConfigParseAndLoadFromMemory(config, config_xml, FcTrue)
        || !FcConfigAppFontAddFile(config, (const FcChar8 *)argv[1])
        || !FcConfigAppFontAddFile(config, (const FcChar8 *)argv[2]))
        return !fail("Fontconfig private in-memory configuration or font files");
    FcFontSet *app_fonts = FcConfigGetFonts(config, FcSetApplication);
    FcFontSet *system_fonts = FcConfigGetFonts(config, FcSetSystem);
    if (!app_fonts || app_fonts->nfont != 2 || (system_fonts && system_fonts->nfont))
        return !fail("Fontconfig explicit two-font isolation");
    /* Pango 1.50.14 starts a background FcInit during font-map construction.
     * Seed this standalone process with the private config first, preventing
     * ambient font directories/configuration from being loaded by that thread.
     * No Fontconfig files or system configuration are changed. The map also
     * receives the config explicitly; it never relies on an ambient default. */
    if (!FcConfigSetCurrent(config)) return !fail("Fontconfig process-local initialization");
    PangoFontMap *map = pango_cairo_font_map_new_for_font_type(CAIRO_FONT_TYPE_FT);
    if (!map || !PANGO_IS_FC_FONT_MAP(map)) return !fail("PangoCairo FreeType font map");
    pango_fc_font_map_set_config(PANGO_FC_FONT_MAP(map), config);
    if (pango_fc_font_map_get_config(PANGO_FC_FONT_MAP(map)) != config)
        return !fail("Pango explicit Fontconfig binding");
    pango_cairo_font_map_set_resolution(PANGO_CAIRO_FONT_MAP(map), 96.0);
    PangoContext *context = pango_font_map_create_context(map);
    pango_context_set_language(context, pango_language_from_string("en"));
    pango_context_set_base_dir(context, PANGO_DIRECTION_LTR);
    cairo_font_options_t *options = cairo_font_options_create();
    cairo_font_options_set_antialias(options, CAIRO_ANTIALIAS_GRAY);
    cairo_font_options_set_hint_style(options, CAIRO_HINT_STYLE_NONE);
    cairo_font_options_set_hint_metrics(options, CAIRO_HINT_METRICS_OFF);
    pango_cairo_context_set_font_options(context, options);
    cairo_font_options_destroy(options);
    PangoLayout *layout = pango_layout_new(context);
    PangoFontDescription *description = pango_font_description_from_string("WesnothLayoutProbe");
    pango_font_description_set_absolute_size(description, 24 * PANGO_SCALE);
    pango_layout_set_font_description(layout, description);
    pango_font_description_free(description);
    pango_layout_set_auto_dir(layout, FALSE);
    int good = test_shaping(layout, argv[1], argv[2]) && test_wrapping(layout) && test_rendering(layout);
    g_object_unref(layout);
    g_object_unref(context);
    g_object_unref(map);
    FcConfigDestroy(config);
    if (!good) return 1;
    puts("PASS wesnoth-android-text-layout");
    return 0;
}
