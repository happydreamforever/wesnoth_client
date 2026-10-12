// Authored in-memory fixtures exercise reusable game codecs without Java,
// filesystem assets, networking, a window, or an audio output device.
#define SDL_MAIN_HANDLED
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <SDL3_image/SDL_image.h>
#include <SDL3_mixer/SDL_mixer.h>
#include <ogg/ogg.h>
#include <vorbis/vorbisenc.h>
#include <vorbis/vorbisfile.h>
#include <webp/encode.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#if !defined(__ANDROID__) || !defined(__x86_64__)
#error "The media checkpoint must target Android x86_64."
#endif
static_assert(__ANDROID_API__ == 23, "The selected minimum API is 23.");
static_assert(SDL_VERSION == SDL_VERSIONNUM(3, 4, 2), "Use SDL 3.4.2.");
static_assert(SDL_IMAGE_VERSION == SDL_VERSIONNUM(3, 4, 0), "Use SDL_image 3.4.0.");
static_assert(SDL_MIXER_VERSION == SDL_VERSIONNUM(3, 2, 0), "Use SDL_mixer 3.2.0.");

namespace {
constexpr int image_size = 32;
constexpr int audio_rate = 22050;
constexpr int audio_frames = 4096;
using Bytes = std::vector<unsigned char>;
using Surface = std::unique_ptr<SDL_Surface, decltype(&SDL_DestroySurface)>;
using Stream = std::unique_ptr<SDL_IOStream, decltype(&SDL_CloseIO)>;

void require(bool condition, const std::string& message)
{
    if(!condition) {
        throw std::runtime_error(message + ": " + SDL_GetError());
    }
}

Surface make_image(bool jpeg)
{
    Surface surface(SDL_CreateSurface(image_size, image_size, SDL_PIXELFORMAT_RGBA32), SDL_DestroySurface);
    require(surface != nullptr, "create authored image");
    for(int y = 0; y < image_size; ++y) {
        auto* row = static_cast<unsigned char*>(surface->pixels) + y * surface->pitch;
        for(int x = 0; x < image_size; ++x) {
            row[4 * x + 0] = jpeg ? 80 : static_cast<unsigned char>(x * 7 + y);
            row[4 * x + 1] = jpeg ? 145 : static_cast<unsigned char>(y * 7 + x);
            row[4 * x + 2] = jpeg ? 210 : static_cast<unsigned char>((x + y) * 3);
            row[4 * x + 3] = jpeg ? 255 : ((x + y) % 2 ? 255 : 128);
        }
    }
    return surface;
}

Bytes save_image(SDL_Surface* image, bool jpeg)
{
    Stream stream(SDL_IOFromDynamicMem(), SDL_CloseIO);
    require(stream != nullptr, "create image output stream");
    require(jpeg ? IMG_SaveJPG_IO(image, stream.get(), false, 95) : IMG_SavePNG_IO(image, stream.get(), false),
            jpeg ? "encode JPEG" : "encode PNG");
    const Sint64 size = SDL_GetIOSize(stream.get());
    auto* data = static_cast<unsigned char*>(SDL_GetPointerProperty(
        SDL_GetIOProperties(stream.get()), SDL_PROP_IOSTREAM_DYNAMIC_MEMORY_POINTER, nullptr));
    require(size > 0 && size < 1'000'000 && data != nullptr, "encoded image data");
    return Bytes(data, data + static_cast<std::size_t>(size));
}

void check_image(const Bytes& encoded, SDL_Surface* original, const char* type, int tolerance)
{
    SDL_IOStream* stream = SDL_IOFromConstMem(encoded.data(), encoded.size());
    require(stream != nullptr, "create image input stream");
    Surface decoded(IMG_LoadTyped_IO(stream, true, type), SDL_DestroySurface);
    require(decoded != nullptr, std::string("decode ") + type);
    Surface rgba(SDL_ConvertSurface(decoded.get(), SDL_PIXELFORMAT_RGBA32), SDL_DestroySurface);
    require(rgba != nullptr && rgba->w == original->w && rgba->h == original->h,
            std::string(type) + " dimensions/format");
    for(int y = 0; y < original->h; ++y) {
        const auto* expected = static_cast<unsigned char*>(original->pixels) + y * original->pitch;
        const auto* actual = static_cast<unsigned char*>(rgba->pixels) + y * rgba->pitch;
        for(int x = 0; x < original->w * 4; ++x) {
            require(std::abs(static_cast<int>(actual[x]) - static_cast<int>(expected[x])) <= tolerance,
                    std::string(type) + " decoded pixel differs");
        }
    }
}

void images()
{
    auto image = make_image(false);
    check_image(save_image(image.get(), false), image.get(), "PNG", 0);
    std::cout << "PASS PNG roundtrip\n";

    auto jpeg = make_image(true);
    check_image(save_image(jpeg.get(), true), jpeg.get(), "JPG", 10);
    std::cout << "PASS JPEG roundtrip\n";

    unsigned char* output = nullptr;
    const std::size_t size = WebPEncodeLosslessRGBA(static_cast<unsigned char*>(image->pixels),
        image->w, image->h, image->pitch, &output);
    const std::unique_ptr<unsigned char, decltype(&WebPFree)> memory(output, WebPFree);
    require(size > 0 && output != nullptr, "encode lossless WebP");
    check_image(Bytes(output, output + size), image.get(), "WEBP", 0);
    std::cout << "PASS WebP roundtrip\n";
}

struct Encoder {
    vorbis_info info{};
    vorbis_comment comment{};
    vorbis_dsp_state dsp{};
    vorbis_block block{};
    ogg_stream_state ogg{};
    bool dsp_ready = false;
    bool block_ready = false;
    bool ogg_ready = false;

    Encoder() { vorbis_info_init(&info); vorbis_comment_init(&comment); }
    ~Encoder()
    {
        if(ogg_ready) ogg_stream_clear(&ogg);
        if(block_ready) vorbis_block_clear(&block);
        if(dsp_ready) vorbis_dsp_clear(&dsp);
        vorbis_comment_clear(&comment);
        vorbis_info_clear(&info);
    }
};

void append_page(Bytes& bytes, const ogg_page& page)
{
    require(page.header_len >= 0 && page.body_len >= 0, "valid encoded Ogg page lengths");
    bytes.insert(bytes.end(), page.header, page.header + page.header_len);
    bytes.insert(bytes.end(), page.body, page.body + page.body_len);
    require(bytes.size() <= 1'000'000, "authored Ogg fixture size");
}

Bytes encode_audio()
{
    Encoder encoder;
    require(vorbis_encode_init_vbr(&encoder.info, 1, audio_rate, 0.3f) == 0, "initialize Vorbis encoder");
    require(vorbis_analysis_init(&encoder.dsp, &encoder.info) == 0, "initialize Vorbis analysis");
    encoder.dsp_ready = true;
    require(vorbis_block_init(&encoder.dsp, &encoder.block) == 0, "initialize Vorbis block");
    encoder.block_ready = true;
    require(ogg_stream_init(&encoder.ogg, 0x5745534e) == 0, "initialize Ogg stream");
    encoder.ogg_ready = true;
    ogg_packet header{}, comments{}, setup{};
    require(vorbis_analysis_headerout(&encoder.dsp, &encoder.comment, &header, &comments, &setup) == 0,
            "write Vorbis headers");
    require(ogg_stream_packetin(&encoder.ogg, &header) == 0 &&
            ogg_stream_packetin(&encoder.ogg, &comments) == 0 &&
            ogg_stream_packetin(&encoder.ogg, &setup) == 0, "queue Vorbis headers");
    Bytes result;
    ogg_page page{};
    while(ogg_stream_flush(&encoder.ogg, &page)) append_page(result, page);

    float** buffer = vorbis_analysis_buffer(&encoder.dsp, audio_frames);
    require(buffer != nullptr, "allocate encoder samples");
    for(int i = 0; i < audio_frames; ++i) {
        buffer[0][i] = 0.3f * static_cast<float>(std::sin(6.283185307179586 * 440.0 * i / audio_rate));
    }
    require(vorbis_analysis_wrote(&encoder.dsp, audio_frames) == 0 &&
            vorbis_analysis_wrote(&encoder.dsp, 0) == 0, "submit authored samples and end-of-stream");
    bool eos = false;
    int available = 0;
    while((available = vorbis_analysis_blockout(&encoder.dsp, &encoder.block)) == 1) {
        require(vorbis_analysis(&encoder.block, nullptr) == 0 && vorbis_bitrate_addblock(&encoder.block) == 0,
                "encode Vorbis block");
        ogg_packet packet{};
        while(vorbis_bitrate_flushpacket(&encoder.dsp, &packet)) {
            require(ogg_stream_packetin(&encoder.ogg, &packet) == 0, "queue encoded Vorbis packet");
            while(ogg_stream_pageout(&encoder.ogg, &page)) {
                append_page(result, page);
                eos = eos || ogg_page_eos(&page);
            }
        }
    }
    require(available == 0, "complete Vorbis analysis");
    while(ogg_stream_flush(&encoder.ogg, &page)) {
        append_page(result, page);
        eos = eos || ogg_page_eos(&page);
    }
    require(eos && !result.empty(), "complete authored Ogg stream");
    return result;
}

struct MemoryAudio { const Bytes& bytes; std::size_t position = 0; };
std::size_t audio_read(void* output, std::size_t size, std::size_t count, void* opaque)
{
    auto& input = *static_cast<MemoryAudio*>(opaque);
    if(size == 0) return 0;
    const std::size_t items = std::min(count, (input.bytes.size() - input.position) / size);
    std::memcpy(output, input.bytes.data() + input.position, items * size);
    input.position += items * size;
    return items;
}
int audio_seek(void* opaque, ogg_int64_t offset, int origin)
{
    auto& input = *static_cast<MemoryAudio*>(opaque);
    const ogg_int64_t base = origin == SEEK_SET ? 0 : origin == SEEK_CUR ?
        static_cast<ogg_int64_t>(input.position) : static_cast<ogg_int64_t>(input.bytes.size());
    if((origin != SEEK_SET && origin != SEEK_CUR && origin != SEEK_END) ||
       offset < -base || offset > static_cast<ogg_int64_t>(input.bytes.size()) - base) return -1;
    input.position = static_cast<std::size_t>(base + offset);
    return 0;
}
long audio_tell(void* opaque)
{
    return static_cast<long>(static_cast<MemoryAudio*>(opaque)->position);
}

void decode_audio(const Bytes& bytes)
{
    MemoryAudio input{bytes};
    OggVorbis_File file{};
    const ov_callbacks callbacks{audio_read, audio_seek, nullptr, audio_tell};
    require(ov_open_callbacks(&input, &file, nullptr, 0, callbacks) == 0, "open authored Ogg with Vorbisfile");
    const std::unique_ptr<OggVorbis_File, decltype(&ov_clear)> opened(&file, ov_clear);
    const vorbis_info* info = ov_info(&file, -1);
    require(info != nullptr && info->channels == 1 && info->rate == audio_rate, "Vorbis decoded audio format");
    require(ov_pcm_total(&file, -1) == audio_frames, "Vorbis total sample frames");
    long total = 0;
    double energy = 0;
    for(;;) {
        float** pcm = nullptr;
        int section = 0;
        const long count = ov_read_float(&file, &pcm, 1024, &section);
        require(count >= 0, "decode Vorbis samples");
        if(count == 0) break;
        require(pcm != nullptr && section == 0 && total + count <= audio_frames, "Vorbis decoded bounds");
        for(long i = 0; i < count; ++i) {
            require(std::isfinite(pcm[0][i]), "finite Vorbis samples");
            energy += pcm[0][i] * pcm[0][i];
        }
        total += count;
    }
    require(total == audio_frames && energy / total > 0.01 && energy / total < 0.1,
            "Vorbis complete non-silent authored signal");
    std::cout << "PASS Ogg Vorbis decode\n";
}

void mix_audio(const Bytes& bytes)
{
    require(MIX_Init(), "initialize SDL_mixer codecs");
    struct MixerCleanup { ~MixerCleanup() { MIX_Quit(); } } cleanup;
    const SDL_AudioSpec spec{SDL_AUDIO_F32, 1, audio_rate};
    MIX_Mixer* mixer = MIX_CreateMixer(&spec);
    require(mixer != nullptr, "create offline mixer without audio device");
    SDL_IOStream* stream = SDL_IOFromConstMem(bytes.data(), bytes.size());
    require(stream != nullptr, "create Ogg mixer input stream");
    MIX_Audio* audio = MIX_LoadAudio_IO(mixer, stream, false, true);
    require(audio != nullptr, "load Ogg through SDL_mixer");
    SDL_AudioSpec actual{};
    require(MIX_GetAudioFormat(audio, &actual) && actual.channels == 1 && actual.freq == audio_rate,
            "SDL_mixer decoded format");
    require(MIX_GetAudioDuration(audio) == audio_frames, "SDL_mixer Ogg duration");
    MIX_Track* track = MIX_CreateTrack(mixer);
    require(track != nullptr && MIX_SetTrackAudio(track, audio) && MIX_PlayTrack(track, 0), "play offline mixer track");
    long total = 0;
    double energy = 0;
    for(int block = 0; block < 32 && MIX_TrackPlaying(track); ++block) {
        float samples[1024]{};
        const int count = MIX_Generate(mixer, samples, sizeof(samples));
        require(count >= 0 && count <= static_cast<int>(sizeof(samples)) && count % sizeof(float) == 0,
                "generate offline mixer samples");
        const int frames = count / sizeof(float);
        for(int i = 0; i < frames; ++i) {
            require(std::isfinite(samples[i]), "finite mixer samples");
            energy += samples[i] * samples[i];
        }
        total += frames;
    }
    require(!MIX_TrackPlaying(track) && total == audio_frames && energy / total > 0.01 && energy / total < 0.1,
            "complete non-silent offline mixer output");
    std::cout << "PASS SDL mixer offline render\n";
}
} // namespace

int main()
{
    std::cout << "WESNOTH_ANDROID_MEDIA version=1.19.26 abi=x86_64 min_api=23\n";
    try {
        SDL_SetMainReady();
        require(SDL_Init(0), "initialize SDL without device subsystems");
        require(SDL_GetVersion() == SDL_VERSION && IMG_Version() == SDL_IMAGE_VERSION && MIX_Version() == SDL_MIXER_VERSION,
                "matching SDL/image/mixer runtime versions");
        images();
        const Bytes audio = encode_audio();
        decode_audio(audio);
        mix_audio(audio);
        SDL_Quit();
        std::cout << "PASS wesnoth-android-media\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "FAIL wesnoth-android-media: " << error.what() << '\n';
        SDL_Quit();
        return 1;
    }
}
