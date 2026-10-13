#include <boost/asio/io_context.hpp>
#include <boost/asio/post.hpp>
#include <boost/asio/steady_timer.hpp>
#include <boost/atomic.hpp>
#include <boost/charconv.hpp>
#include <boost/chrono.hpp>
#include <boost/coroutine/asymmetric_coroutine.hpp>
#include <boost/filesystem.hpp>
#include <boost/filesystem/fstream.hpp>
#include <boost/iostreams/copy.hpp>
#include <boost/iostreams/device/array.hpp>
#include <boost/iostreams/device/back_inserter.hpp>
#include <boost/iostreams/filter/bzip2.hpp>
#include <boost/iostreams/filter/gzip.hpp>
#include <boost/iostreams/filtering_stream.hpp>
#include <boost/locale.hpp>
#include <boost/locale/localization_backend.hpp>
#include <boost/program_options.hpp>
#include <boost/process/v2/environment.hpp>
#include <boost/random/mersenne_twister.hpp>
#include <boost/random/random_device.hpp>
#include <boost/regex.hpp>
#include <boost/thread.hpp>
#include <boost/version.hpp>
#include <unicode/uclean.h>
#include <unicode/udata.h>
#include <unicode/putil.h>
#include <unicode/uversion.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#if !defined(__ANDROID__) || !defined(__x86_64__) || __ANDROID_API__ != 23
#error "Build the selected Android x86_64/API23 probe."
#endif
#if BOOST_VERSION != 108800
#error "This probe requires the pinned Boost 1.88.0 headers."
#endif
#if U_ICU_VERSION_MAJOR_NUM != 78 || U_ICU_VERSION_MINOR_NUM != 3 || U_ICU_VERSION_PATCHLEVEL_NUM != 0
#error "This probe requires the pinned ICU 78.3 headers."
#endif

extern "C" const char U_ICUDATA_ENTRY_POINT[];

static void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

static void locale_and_data()
{
    UErrorCode status = U_ZERO_ERROR;
    udata_setFileAccess(UDATA_NO_FILES, &status);
    u_setDataDirectory("/wesnoth-boost-probe-no-external-data");
    require(status == U_ZERO_ERROR
            && (reinterpret_cast<std::uintptr_t>(U_ICUDATA_ENTRY_POINT) & 15) == 0,
            "ICU embedded data access mode or alignment");
    udata_setCommonData(U_ICUDATA_ENTRY_POINT, &status);
    require(status == U_ZERO_ERROR, "ICU embedded data registration");
    u_init(&status);
    require(status == U_ZERO_ERROR, "ICU embedded data initialization");
    UVersionInfo version{};
    const uint8_t expected[] = {78, 3, 0, 0};
    u_getVersion(version);
    require(std::memcmp(version, expected, sizeof(version)) == 0, "ICU runtime version");

    namespace locale = boost::locale;
    auto manager = locale::localization_backend_manager::global();
    const auto backends = manager.get_all_backends();
    require(backends.size() == 1 && backends.front() == "icu", "Boost.Locale ICU-only backend");
    manager.select("icu");
    locale::generator generator(manager);
    const std::locale german = generator("de_DE.UTF-8");
    const std::locale swedish = generator("sv_SE.UTF-8");
    require(std::use_facet<locale::info>(german).language() == "de"
            && std::use_facet<locale::info>(swedish).language() == "sv", "Boost.Locale language selection");
    require(locale::normalize(std::string("Cafe\xcc\x81"), locale::norm_nfc, german) == "Caf\xc3\xa9",
            "Boost.Locale ICU NFC normalization");
    require(locale::fold_case(std::string("Stra\xc3\x9f" "e"), german) == "strasse",
            "Boost.Locale ICU expanding case fold");
    const std::string umlaut = "\xc3\xa4";
    locale::comparator<char> german_order(german, locale::collate_level::primary);
    locale::comparator<char> swedish_order(swedish, locale::collate_level::primary);
    require(german_order(umlaut, "z") && swedish_order("z", umlaut),
            "Boost.Locale distinct German and Swedish collation");
    require(locale::conv::to_utf<char>(std::string("caf\xe9"), "ISO-8859-1", locale::conv::stop)
            == "caf\xc3\xa9", "Boost.Locale ICU encoding conversion");
    bool invalid_rejected = false;
    try {
        (void)locale::conv::to_utf<char>(std::string("\xf0\x28\x8c\x28"), "UTF-8", locale::conv::stop);
    } catch (const locale::conv::conversion_error&) { invalid_rejected = true; }
    require(invalid_rejected, "Boost.Locale malformed UTF-8 rejection");
    std::printf("Boost=%s ICU=%s; ICU-only locale backend; embedded data; external data files disabled\n",
                BOOST_LIB_VERSION, U_ICU_VERSION);
    std::puts("PASS Boost.Locale ICU backend and embedded data");
}

struct OwnedDirectory {
    boost::filesystem::path root;
    OwnedDirectory()
    {
        root = boost::filesystem::current_path()
            / boost::filesystem::unique_path("wesnoth-boost-files-%%%%-%%%%-%%%%");
        require(boost::filesystem::create_directory(root), "Boost.Filesystem unique directory creation");
    }
    ~OwnedDirectory()
    {
        boost::system::error_code ignored;
        // Only these exact names inside the directory created by this process
        // are owned. Do not recursively remove preexisting paths.
        boost::filesystem::remove(root / "original.bin", ignored);
        boost::filesystem::remove(root / "renamed.bin", ignored);
        boost::filesystem::remove(root, ignored);
    }
};

static void filesystem_operations()
{
    namespace fs = boost::filesystem;
    OwnedDirectory directory;
    const fs::path original = directory.root / "original.bin";
    const fs::path renamed = directory.root / "renamed.bin";
    const std::string payload("Wesnoth\0offline\n", 16);
    {
        fs::ofstream output(original, std::ios::binary);
        output.write(payload.data(), static_cast<std::streamsize>(payload.size()));
        output.close();
        require(!output.fail(), "Boost.Filesystem binary write and close");
    }
    require(fs::is_regular_file(original) && fs::file_size(original) == payload.size(),
            "Boost.Filesystem written file metadata");
    fs::rename(original, renamed);
    require(!fs::exists(original) && fs::is_regular_file(renamed), "Boost.Filesystem rename");
    {
        fs::ifstream input(renamed, std::ios::binary);
        const std::string recovered((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        require(!input.bad() && recovered == payload, "Boost.Filesystem binary readback");
    }
    namespace environment = boost::process::v2::environment;
    // Supply a private search list; inspect permission bits but never execute
    // this file or consult the ambient PATH/environment.
    fs::permissions(renamed, fs::owner_read | fs::owner_write | fs::owner_exe);
    const std::vector<environment::key_value_pair> search{
        environment::key_value_pair("PATH=" + directory.root.string())};
    require(environment::find_executable("renamed.bin", search).string() == renamed.string()
            && environment::find_executable("does-not-exist", search).empty(),
            "Boost.Process v2 private executable search and missing result");
    require(fs::remove(renamed) && fs::remove(directory.root), "Boost.Filesystem owned file cleanup");
    require(!fs::exists(directory.root), "Boost.Filesystem cleanup result");
    std::puts("PASS Boost.Filesystem and Process scoped file operations");
}

static void options_random_charconv_regex()
{
    namespace po = boost::program_options;
    po::options_description options("probe");
    options.add_options()("port", po::value<int>()->required(), "port")
                         ("name", po::value<std::string>()->required(), "name");
    po::variables_map values;
    po::store(po::command_line_parser(std::vector<std::string>{"--port", "15000", "--name", "offline guest"})
                  .options(options).run(), values);
    po::notify(values);
    require(values["port"].as<int>() == 15000 && values["name"].as<std::string>() == "offline guest",
            "Boost.ProgramOptions typed parsing");
    bool bad_option_rejected = false;
    try {
        po::variables_map bad;
        po::store(po::command_line_parser(std::vector<std::string>{"--port", "invalid", "--name", "guest"})
                      .options(options).run(), bad);
        po::notify(bad);
    } catch (const po::invalid_option_value&) { bad_option_rejected = true; }
    require(bad_option_rejected, "Boost.ProgramOptions invalid integer rejection");

    boost::random::mt19937 random(5489u);
    require(random() == 3499211612u, "Boost.Random deterministic generator");
    boost::random::random_device entropy;
    (void)entropy(); // Exercise the compiled OS-entropy implementation, without asserting random values.

    char buffer[64]{};
    const auto written = boost::charconv::to_chars(buffer, buffer + sizeof(buffer), 42.125);
    require(written.ec == std::errc() && std::string(buffer, written.ptr) == "42.125",
            "Boost.Charconv floating serialization");
    double recovered = 0;
    const auto parsed = boost::charconv::from_chars(buffer, written.ptr, recovered);
    require(parsed.ec == std::errc() && parsed.ptr == written.ptr && recovered == 42.125,
            "Boost.Charconv floating round trip");
    const std::string invalid = "not-a-number";
    const auto rejected = boost::charconv::from_chars(invalid.data(), invalid.data() + invalid.size(), recovered);
    require(rejected.ec == std::errc::invalid_argument && rejected.ptr == invalid.data(),
            "Boost.Charconv invalid input rejection");

    const boost::regex expression("^([A-Za-z]+)-([0-9]{2})$");
    boost::smatch captures;
    const std::string good = "Wesnoth-26";
    require(boost::regex_match(good, captures, expression) && captures[1] == "Wesnoth" && captures[2] == "26",
            "Boost.Regex match and captures");
    require(!boost::regex_match(std::string("Wesnoth-XX"), expression), "Boost.Regex non-digit rejection");
    std::puts("PASS Boost.ProgramOptions Random Charconv and Regex");
}

template<typename Compressor>
static std::string compress(const std::string& input)
{
    std::string output;
    boost::iostreams::filtering_ostream stream;
    stream.push(Compressor());
    stream.push(boost::iostreams::back_inserter(output));
    stream.write(input.data(), static_cast<std::streamsize>(input.size()));
    stream.reset();
    return output;
}

template<typename Decompressor>
static std::string decompress(const std::string& input)
{
    boost::iostreams::filtering_istream stream;
    stream.push(Decompressor());
    stream.push(boost::iostreams::array_source(input.data(), input.size()));
    std::ostringstream output;
    boost::iostreams::copy(stream, output);
    return output.str();
}

static void compressed_streams()
{
    namespace io = boost::iostreams;
    std::string payload;
    for (int i = 0; i != 128; ++i) payload.append("Wesnoth\0offline turn\n", 21);
    std::string gzip = compress<io::gzip_compressor>(payload);
    std::string bzip = compress<io::bzip2_compressor>(payload);
    require(gzip.size() < payload.size() && decompress<io::gzip_decompressor>(gzip) == payload,
            "Boost.Iostreams gzip binary round trip");
    require(bzip.size() < payload.size() && decompress<io::bzip2_decompressor>(bzip) == payload,
            "Boost.Iostreams bzip2 binary round trip");
    gzip[0] = '\0';
    bzip[0] = '\0';
    bool gzip_rejected = false, bzip_rejected = false;
    try { (void)decompress<io::gzip_decompressor>(gzip); }
    catch (const io::gzip_error&) { gzip_rejected = true; }
    try { (void)decompress<io::bzip2_decompressor>(bzip); }
    catch (const io::bzip2_error&) { bzip_rejected = true; }
    require(gzip_rejected && bzip_rejected, "Boost.Iostreams malformed compression header rejection");
    std::puts("PASS Boost.Iostreams gzip and bzip2 round trips");
}

static void threads_coroutines_asio()
{
    int result = 0;
    boost::mutex mutex;
    boost::atomic<std::uint64_t> ready(0);
    const auto started = boost::chrono::steady_clock::now();
    boost::thread worker([&] {
        boost::lock_guard<boost::mutex> lock(mutex);
        result = 42;
        ready.store(1, boost::memory_order_release);
        ready.notify_one();
    });
    (void)ready.wait(0, boost::memory_order_acquire);
    worker.join();
    require(result == 42 && ready.load() == 1
            && boost::chrono::steady_clock::now() >= started,
            "Boost.Thread joined worker with Atomic wake and monotonic Chrono");

    using Coroutine = boost::coroutines::asymmetric_coroutine<int>;
    Coroutine::pull_type source([](Coroutine::push_type& yield) {
        yield(7);
        yield(11);
    });
    require(source && source.get() == 7, "Boost.Coroutine initial Context transfer");
    source();
    require(source && source.get() == 11, "Boost.Coroutine resumed Context transfer");
    source();
    require(!source, "Boost.Coroutine completion");

    boost::asio::io_context context;
    boost::asio::steady_timer timer(context, std::chrono::milliseconds(1));
    bool posted = false, elapsed = false;
    boost::asio::post(context, [&] { posted = true; });
    timer.async_wait([&](const boost::system::error_code& error) {
        require(!error, "Boost.Asio timer result");
        elapsed = true;
    });
    const auto completed = context.run();
    require(completed == 2 && posted && elapsed, "Boost.Asio local event dispatch and timer");
    std::puts("PASS Boost.Thread Context Coroutine and Asio");
}

int main(int argc, char**)
{
    if (argc != 1) {
        std::fprintf(stderr, "FAIL Boost probe accepts no arguments or external asset paths\n");
        return 1;
    }
    std::puts("WESNOTH_ANDROID_BOOST version=1.19.26 abi=x86_64 min_api=23");
    try {
        locale_and_data();
        filesystem_operations();
        options_random_charconv_regex();
        compressed_streams();
        threads_coroutines_asio();
        u_cleanup();
        std::puts("PASS wesnoth-android-boost");
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL %s\n", error.what());
        return 1;
    }
}
