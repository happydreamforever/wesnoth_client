// Native build/runtime checkpoint for the restored Wesnoth Android project.
// No network, game data, Android UI, or server connection is used here.
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <vector>

#include <bzlib.h>
#include <zlib.h>

#if !defined(__ANDROID__) || !defined(__x86_64__)
#error This checkpoint requires Android x86_64.
#endif
static_assert(__ANDROID_API__ == 23, "The native checkpoint must retain minimum API 23.");
static_assert(sizeof(void*) == 8, "The native checkpoint must be 64-bit.");

namespace {
void require(bool condition, const char* message)
{
    if(!condition) {
        throw std::runtime_error(message);
    }
}

void check_stl()
{
    std::vector<std::string> names{"wesnoth", "android", "offline"};
    std::sort(names.begin(), names.end());
    require(names.size() == 3 && names.front() == "android" && names.back() == "wesnoth",
            "C++ STL sorting/string check failed");
    std::puts("PASS C++ STL");
}

void check_exceptions()
{
    int destructors = 0;
    struct UnwindCheck {
        int& count;
        ~UnwindCheck() { ++count; }
    };
    bool caught = false;
    try {
        UnwindCheck guard{destructors};
        throw std::runtime_error("wesnoth exception checkpoint");
    } catch(const std::runtime_error& error) {
        caught = std::string(error.what()) == "wesnoth exception checkpoint";
    }
    require(caught && destructors == 1, "C++ exception/unwind check failed");
    std::puts("PASS C++ exceptions");
}

std::vector<char> sample_data()
{
    // Include embedded NULs, every byte value, and repeated data. Neither codec
    // may treat the input as a NUL-terminated string or lose binary contents.
    std::vector<char> sample(16384);
    for(std::size_t i = 0; i < sample.size(); ++i) {
        sample[i] = static_cast<char>(static_cast<std::uint8_t>((i * 37u) ^ (i / 256u)));
    }
    std::fill(sample.begin() + 4096, sample.begin() + 8192, 'W');
    return sample;
}

void check_bzip2(const std::vector<char>& original)
{
    const std::string version = BZ2_bzlibVersion();
    require(version.rfind("1.0.8,", 0) == 0, "Unexpected bzip2 library version");
    std::printf("INFO bzip2 runtime=%s\n", version.c_str());
    std::vector<char> source = original; // bzip2's buffer API takes char*.
    std::vector<char> compressed(source.size() + source.size() / 100 + 601);
    unsigned int compressed_size = static_cast<unsigned int>(compressed.size());
    int status = BZ2_bzBuffToBuffCompress(compressed.data(), &compressed_size, source.data(),
                                        static_cast<unsigned int>(source.size()), 9, 0, 30);
    require(status == BZ_OK, "bzip2 compression failed");
    std::vector<char> restored(source.size());
    unsigned int restored_size = static_cast<unsigned int>(restored.size());
    status = BZ2_bzBuffToBuffDecompress(restored.data(), &restored_size, compressed.data(),
                                       compressed_size, 0, 0);
    require(status == BZ_OK && restored_size == original.size() && restored == original,
            "bzip2 decompression did not reproduce the binary input");
    std::puts("PASS bzip2 roundtrip");
}

void check_zlib(const std::vector<char>& original)
{
    std::printf("INFO zlib headers=%s runtime=%s\n", ZLIB_VERSION, zlibVersion());
    uLongf compressed_size = compressBound(static_cast<uLong>(original.size()));
    std::vector<Bytef> compressed(compressed_size);
    int status = compress2(compressed.data(), &compressed_size,
                           reinterpret_cast<const Bytef*>(original.data()),
                           static_cast<uLong>(original.size()), Z_BEST_COMPRESSION);
    require(status == Z_OK, "zlib compression failed");
    std::vector<char> restored(original.size());
    uLongf restored_size = static_cast<uLongf>(restored.size());
    status = uncompress(reinterpret_cast<Bytef*>(restored.data()), &restored_size,
                        compressed.data(), compressed_size);
    require(status == Z_OK && restored_size == original.size() && restored == original,
            "zlib decompression did not reproduce the binary input");
    std::puts("PASS zlib roundtrip");
}
} // namespace

int main()
{
    std::puts("WESNOTH_NATIVE_BOOTSTRAP version=1.19.26 abi=x86_64 min_api=23");
    try {
        check_stl();
        check_exceptions();
        const auto sample = sample_data();
        check_bzip2(sample);
        check_zlib(sample);
        std::puts("PASS wesnoth-native-bootstrap");
        return 0;
    } catch(const std::exception& error) {
        std::fprintf(stderr, "FAIL wesnoth-native-bootstrap: %s\n", error.what());
    } catch(...) {
        std::fputs("FAIL wesnoth-native-bootstrap: unknown C++ exception\n", stderr);
    }
    return 1;
}
