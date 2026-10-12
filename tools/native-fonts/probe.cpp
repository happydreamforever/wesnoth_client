// Shapes authored text and rasterizes glyphs from the user's verified game
// fonts. This needs no Java activity, window, system fonts or network service.
#include <ft2build.h>
#include FT_FREETYPE_H
#include <hb.h>
#include <hb-ft.h>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#if !defined(__ANDROID__) || !defined(__x86_64__)
#error "The font checkpoint must target Android x86_64."
#endif
static_assert(__ANDROID_API__ == 23, "The selected minimum API is 23.");
static_assert(FREETYPE_MAJOR == 2 && FREETYPE_MINOR == 13 && FREETYPE_PATCH == 0,
              "Use FreeType 2.13.0.");
static_assert(HB_VERSION_MAJOR == 7 && HB_VERSION_MINOR == 1 && HB_VERSION_MICRO == 0,
              "Use HarfBuzz 7.1.0.");

namespace {
constexpr unsigned int font_pixels = 48;
using Face = std::unique_ptr<std::remove_pointer_t<FT_Face>, decltype(&FT_Done_Face)>;
using Font = std::unique_ptr<hb_font_t, decltype(&hb_font_destroy)>;
using Buffer = std::unique_ptr<hb_buffer_t, decltype(&hb_buffer_destroy)>;

void require(bool condition, const std::string& message)
{
    if(!condition) throw std::runtime_error(message);
}

struct Library {
    FT_Library value = nullptr;
    Library() { require(FT_Init_FreeType(&value) == 0, "initialize FreeType"); }
    ~Library() { if(value) FT_Done_FreeType(value); }
};

Face load_font(FT_Library library, const std::string& directory, const char* name)
{
    FT_Face raw = nullptr;
    const std::string path = directory + "/" + name;
    const FT_Error result = FT_New_Face(library, path.c_str(), 0, &raw);
    Face face(raw, FT_Done_Face);
    require(result == 0 && face != nullptr, std::string("load font ") + name);
    require(FT_IS_SCALABLE(face.get()) && face->num_glyphs > 1000, std::string("scalable font ") + name);
    require(FT_Select_Charmap(face.get(), FT_ENCODING_UNICODE) == 0, std::string("Unicode charmap ") + name);
    require(FT_Set_Pixel_Sizes(face.get(), 0, font_pixels) == 0, std::string("set font size ") + name);
    return face;
}

struct Glyph {
    std::uint32_t id;
    std::uint32_t cluster;
    std::int32_t x_advance;
    std::int32_t y_advance;
    std::int32_t x_offset;
    std::int32_t y_offset;
};
using Run = std::vector<Glyph>;

Run shape(FT_Face face, const std::string& text, bool arabic, bool ligatures = true)
{
    Font font(hb_ft_font_create_referenced(face), hb_font_destroy);
    require(font && hb_ft_font_get_face(font.get()) == face, "create HarfBuzz FreeType bridge");
    hb_ft_font_set_load_flags(font.get(), FT_LOAD_DEFAULT);
    Buffer buffer(hb_buffer_create(), hb_buffer_destroy);
    require(buffer && hb_buffer_allocation_successful(buffer.get()), "allocate shaping buffer");
    // Direction is explicit for these two single-script runs. Paragraph bidi
    // resolution, fallback and layout remain the later Pango/FriBidi work.
    hb_buffer_set_direction(buffer.get(), arabic ? HB_DIRECTION_RTL : HB_DIRECTION_LTR);
    hb_buffer_set_script(buffer.get(), arabic ? HB_SCRIPT_ARABIC : HB_SCRIPT_LATIN);
    hb_buffer_set_language(buffer.get(), hb_language_from_string(arabic ? "ar" : "en", -1));
    hb_buffer_add_utf8(buffer.get(), text.data(), static_cast<int>(text.size()), 0, static_cast<int>(text.size()));
    hb_feature_t feature{};
    require(hb_feature_from_string(ligatures ? "liga=1" : "liga=0", -1, &feature), "parse ligature feature");
    hb_shape(font.get(), buffer.get(), &feature, 1);
    require(hb_buffer_allocation_successful(buffer.get()), "complete shaping allocation");
    unsigned int count = 0;
    const auto* infos = hb_buffer_get_glyph_infos(buffer.get(), &count);
    unsigned int position_count = 0;
    const auto* positions = hb_buffer_get_glyph_positions(buffer.get(), &position_count);
    require(count > 0 && count <= text.size() && count == position_count && infos && positions,
            "shaped glyph and position counts");
    Run run;
    std::int64_t advance = 0;
    for(unsigned int i = 0; i < count; ++i) {
        const auto& info = infos[i];
        const auto& pos = positions[i];
        require(info.codepoint > 0 && info.codepoint < static_cast<unsigned long>(face->num_glyphs),
                "shaped glyph must exist and must not be .notdef");
        require(info.cluster < text.size() &&
                (static_cast<unsigned char>(text[info.cluster]) & 0xc0) != 0x80,
                "cluster must identify a UTF-8 character boundary");
        if(i != 0) {
            require(arabic ? info.cluster <= infos[i - 1].cluster : info.cluster >= infos[i - 1].cluster,
                    "clusters must follow the selected run direction");
        }
        require(pos.x_advance >= 0 && pos.x_advance < 65536 && pos.y_advance == 0,
                "bounded horizontal glyph advance");
        advance += pos.x_advance;
        run.push_back({info.codepoint, info.cluster, pos.x_advance, pos.y_advance, pos.x_offset, pos.y_offset});
    }
    require(advance > 0 && advance < 512 * 64, "nonzero bounded shaped run advance");
    return run;
}

Run latin(FT_Face face)
{
    const std::string text = "office";
    const Run enabled = shape(face, text, false);
    const Run disabled = shape(face, text, false, false);
    require(disabled.size() == text.size() && enabled.size() < disabled.size(),
            "Lato ligature substitution must reduce the authored Latin glyph count");
    require(enabled.front().cluster == 0 && enabled.back().cluster == 5, "Latin cluster endpoints");
    std::cout << "PASS HarfBuzz Latin ligatures\n";
    return enabled;
}

Run arabic(FT_Face face)
{
    // U+0633 U+0644 U+0627 U+0645: four authored characters with contextual
    // joining and the lam-alef substitution in the pinned DejaVu Sans font.
    const std::string text = "\xD8\xB3\xD9\x84\xD8\xA7\xD9\x85";
    const Run run = shape(face, text, true);
    require(run.size() >= 2 && run.size() < 4, "Arabic joining/lam-alef substitution glyph count");
    require(run.front().cluster == 6 && run.back().cluster == 0, "Arabic right-to-left cluster endpoints");
    std::vector<FT_UInt> nominal;
    for(FT_ULong codepoint : {0x0633UL, 0x0644UL, 0x0627UL, 0x0645UL}) {
        const auto glyph = FT_Get_Char_Index(face, codepoint);
        require(glyph != 0, "Arabic source characters must exist in DejaVu Sans");
        nominal.push_back(glyph);
    }
    require(std::any_of(run.begin(), run.end(), [&](const Glyph& glyph) {
        return std::find(nominal.begin(), nominal.end(), glyph.id) == nominal.end();
    }), "Arabic shaping must select a contextual or ligature glyph");
    std::cout << "PASS HarfBuzz Arabic shaping\n";
    return run;
}

std::size_t rasterize(FT_Face face, const Run& run)
{
    constexpr int width = 1024;
    constexpr int height = 160;
    std::vector<unsigned char> mask(width * height, 0);
    std::int64_t pen_x = 64 * 64;
    for(const auto& glyph : run) {
        require(FT_Load_Glyph(face, glyph.id, FT_LOAD_DEFAULT) == 0, "load shaped glyph for rasterization");
        require(FT_Render_Glyph(face->glyph, FT_RENDER_MODE_NORMAL) == 0, "rasterize shaped glyph");
        const auto& bitmap = face->glyph->bitmap;
        require(bitmap.pixel_mode == FT_PIXEL_MODE_GRAY && bitmap.num_grays == 256 && bitmap.buffer &&
                bitmap.width > 0 && bitmap.rows > 0 && bitmap.width < 512 && bitmap.rows < 128 &&
                bitmap.pitch >= static_cast<int>(bitmap.width), "nonempty bounded grayscale glyph bitmap");
        const int left = static_cast<int>((pen_x + glyph.x_offset) / 64) + face->glyph->bitmap_left;
        const int top = 100 - glyph.y_offset / 64 - face->glyph->bitmap_top;
        require(left >= 0 && top >= 0 && left + static_cast<int>(bitmap.width) <= width &&
                top + static_cast<int>(bitmap.rows) <= height, "positioned glyph stays inside the authored mask");
        for(unsigned int y = 0; y < bitmap.rows; ++y) {
            for(unsigned int x = 0; x < bitmap.width; ++x) {
                auto& pixel = mask[(top + y) * width + left + x];
                pixel = std::max(pixel, bitmap.buffer[y * bitmap.pitch + x]);
            }
        }
        pen_x += glyph.x_advance;
    }
    const auto ink = static_cast<std::size_t>(std::count_if(mask.begin(), mask.end(), [](unsigned char pixel) {
        return pixel != 0;
    }));
    require(ink > 100 && ink < mask.size() / 2, "rasterized run must contain meaningful nonempty glyph coverage");
    return ink;
}
} // namespace

int main(int argc, char** argv)
{
    std::cout << "WESNOTH_ANDROID_FONTS version=1.19.26 abi=x86_64 min_api=23\n";
    try {
        require(argc == 2 && argv[1][0] != '\0', "usage: wesnoth-fonts-check <font-directory>");
        Library library;
        FT_Int ft_major = 0, ft_minor = 0, ft_patch = 0;
        FT_Library_Version(library.value, &ft_major, &ft_minor, &ft_patch);
        unsigned int hb_major = 0, hb_minor = 0, hb_patch = 0;
        hb_version(&hb_major, &hb_minor, &hb_patch);
        require(ft_major == 2 && ft_minor == 13 && ft_patch == 0 &&
                hb_major == 7 && hb_minor == 1 && hb_patch == 0, "matching FreeType/HarfBuzz runtime versions");
        std::cout << "Font libraries: FreeType 2.13.0; HarfBuzz 7.1.0\n";
        auto lato = load_font(library.value, argv[1], "Lato-Regular.ttf");
        auto dejavu = load_font(library.value, argv[1], "DejaVuSans.ttf");
        std::cout << "PASS FreeType font loading\n";
        const Run latin_run = latin(lato.get());
        const Run arabic_run = arabic(dejavu.get());
        const auto latin_ink = rasterize(lato.get(), latin_run);
        const auto arabic_ink = rasterize(dejavu.get(), arabic_run);
        std::cout << "Raster coverage: Latin=" << latin_ink << "; Arabic=" << arabic_ink << " pixels\n";
        std::cout << "PASS FreeType glyph rasterization\n";
        std::cout << "PASS wesnoth-android-fonts\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "FAIL wesnoth-android-fonts: " << error.what() << '\n';
        return 1;
    }
}
