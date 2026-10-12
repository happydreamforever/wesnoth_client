/* Exercise the static GLib foundation; this does not start Wesnoth. */
#include <glib.h>
#include <glib-object.h>
#include <gio/gio.h>
#include <gmodule.h>
#include <zlib.h>
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

static int unicode_checks(void)
{
    const char text[] = "Wesnoth \xd8\xb9\xd9\x85 \xf0\x9f\x98\x80";
    GError *error = NULL;
    gsize read = 0, written = 0, recovered = 0;
    if (!g_utf8_validate(text, -1, NULL) || g_utf8_strlen(text, -1) != 12 ||
        g_utf8_validate("\xff", 1, NULL))
        return fail("GLib UTF-8 validation or character count");
    char *utf16 = g_convert(text, -1, "UTF-16LE", "UTF-8", &read, &written, &error);
    if (!utf16 || error || read != strlen(text) || written != 26) {
        g_free(utf16); g_clear_error(&error);
        return fail("GLib UTF-8 to UTF-16 conversion");
    }
    char *roundtrip = g_convert(utf16, written, "UTF-8", "UTF-16LE", NULL, &recovered, &error);
    gboolean good = roundtrip && !error && recovered == strlen(text) && !memcmp(roundtrip, text, recovered);
    g_free(utf16); g_free(roundtrip); g_clear_error(&error);
    if (!good) return fail("GLib Unicode roundtrip");
    char *invalid = g_convert("\xff", 1, "UTF-16LE", "UTF-8", NULL, NULL, &error);
    good = !invalid && g_error_matches(error, G_CONVERT_ERROR, G_CONVERT_ERROR_ILLEGAL_SEQUENCE);
    g_free(invalid); g_clear_error(&error);
    if (!good) return fail("GLib invalid Unicode rejection");

    GRegex *regex = g_regex_new("^(?<name>\\p{L}+)-(?<count>[0-9]+)$", 0, 0, &error);
    if (!regex || error) { g_clear_pointer(&regex, g_regex_unref); g_clear_error(&error); return fail("GLib regex creation"); }
    GMatchInfo *match = NULL;
    good = g_regex_match(regex, "\xd8\xb9\xd9\x85-23", 0, &match);
    char *name = g_match_info_fetch_named(match, "name");
    char *count = g_match_info_fetch_named(match, "count");
    good = good && name && count && !strcmp(name, "\xd8\xb9\xd9\x85") && !strcmp(count, "23");
    g_free(name); g_free(count); g_match_info_free(match);
    good = good && !g_regex_match(regex, "123-23", 0, NULL);
    g_regex_unref(regex);
    if (!good) return fail("GLib Unicode regex capture or no-match result");
    puts("PASS GLib Unicode and regex");
    return 0;
}

static int variant_checks(void)
{
    GVariantBuilder builder;
    g_variant_builder_init(&builder, G_VARIANT_TYPE_VARDICT);
    g_variant_builder_add(&builder, "{sv}", "turn", g_variant_new_int32(7));
    g_variant_builder_add(&builder, "{sv}", "player", g_variant_new_string("local-player"));
    GVariant *original = g_variant_ref_sink(g_variant_builder_end(&builder));
    GBytes *bytes = g_variant_get_data_as_bytes(original);
    GVariant *copy = g_variant_ref_sink(g_variant_new_from_bytes(G_VARIANT_TYPE_VARDICT, bytes, FALSE));
    gint32 turn = 0;
    const char *player = NULL;
    gboolean good = g_bytes_get_size(bytes) > 0 && g_variant_is_normal_form(copy) &&
        g_variant_equal(original, copy) && g_variant_lookup(copy, "turn", "i", &turn) && turn == 7 &&
        g_variant_lookup(copy, "player", "&s", &player) && !strcmp(player, "local-player") &&
        !g_variant_lookup(copy, "turn", "&s", &player);
    g_variant_unref(copy); g_bytes_unref(bytes); g_variant_unref(original);
    if (!good) return fail("GLib variant serialization or typed lookup");
    puts("PASS GLib variants");
    return 0;
}

typedef struct { int calls, destroyed, finalized; double offset; } ObjectState;

static double measure(GObject *object, int count, double weight, gpointer data)
{
    ObjectState *state = data;
    if (G_IS_OBJECT(object)) state->calls++;
    return count * weight + state->offset;
}

static void destroy_data(gpointer data) { ((ObjectState *)data)->destroyed++; }
static void weak_notify(gpointer data, GObject *object)
{
    if (object) ((ObjectState *)data)->finalized++;
}

static int object_checks(void)
{
    GType type = g_type_register_static_simple(G_TYPE_OBJECT, "WesnothProbeObject",
        sizeof(GObjectClass), NULL, sizeof(GObject), NULL, 0);
    guint signal = g_signal_new("measure", type, G_SIGNAL_RUN_LAST, 0, NULL, NULL,
        g_cclosure_marshal_generic, G_TYPE_DOUBLE, 2, G_TYPE_INT, G_TYPE_DOUBLE);
    if (!type || !signal) return fail("GObject type or signal registration");
    ObjectState state = {0, 0, 0, 0.5};
    GObject *object = g_object_new(type, NULL);
    g_object_set_data_full(object, "probe-state", &state, destroy_data);
    g_object_weak_ref(object, weak_notify, &state);
    gulong handler = g_signal_connect(object, "measure", G_CALLBACK(measure), &state);
    double result = 0;
    g_signal_emit(object, signal, 0, 7, 2.0, &result);
    gboolean good = handler && state.calls == 1 && result == 14.5;
    g_object_ref(object);
    g_object_unref(object);
    good = good && state.destroyed == 0 && state.finalized == 0;
    g_object_unref(object);
    good = good && state.destroyed == 1 && state.finalized == 1;
    if (!good) return fail("GObject generic signal marshalling or reference lifetime");
    puts("PASS GObject signals and lifetime");
    return 0;
}

static int stream_checks(void)
{
    const char payload[] = "Offline Wesnoth stream: \xd8\xb9\xd9\x85\n";
    GError *error = NULL;
    GOutputStream *memory = g_memory_output_stream_new_resizable();
    GZlibCompressor *compressor = g_zlib_compressor_new(G_ZLIB_COMPRESSOR_FORMAT_GZIP, 6);
    GOutputStream *output = g_converter_output_stream_new(memory, G_CONVERTER(compressor));
    gsize written = 0;
    gboolean good = g_output_stream_write_all(output, payload, sizeof(payload) - 1, &written, NULL, &error) &&
        written == sizeof(payload) - 1 && g_output_stream_close(output, NULL, &error);
    gsize size = g_memory_output_stream_get_data_size(G_MEMORY_OUTPUT_STREAM(memory));
    gpointer compressed = NULL;
    if (good && !error && size > 0)
        compressed = g_memory_output_stream_steal_data(G_MEMORY_OUTPUT_STREAM(memory));
    g_object_unref(output); g_object_unref(compressor); g_object_unref(memory);
    g_clear_error(&error);
    if (!good || !compressed) { g_free(compressed); return fail("GIO gzip output stream"); }

    GInputStream *source = g_memory_input_stream_new_from_data(compressed, size, g_free);
    GZlibDecompressor *decompressor = g_zlib_decompressor_new(G_ZLIB_COMPRESSOR_FORMAT_GZIP);
    GInputStream *input = g_converter_input_stream_new(source, G_CONVERTER(decompressor));
    char recovered[sizeof(payload)] = {0};
    gsize read = 0;
    good = g_input_stream_read_all(input, recovered, sizeof(recovered), &read, NULL, &error) &&
        !error && read == sizeof(payload) - 1 && !memcmp(recovered, payload, read);
    g_clear_error(&error);
    good = g_input_stream_close(input, NULL, &error) && !error && good;
    g_clear_error(&error);
    gssize after_close = g_input_stream_read(input, recovered, 1, NULL, &error);
    good = good && after_close == -1 && g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CLOSED);
    g_clear_error(&error);
    g_object_unref(input); g_object_unref(decompressor); g_object_unref(source);
    if (!good) return fail("GIO gzip roundtrip or closed-stream rejection");
    puts("PASS GIO streams and compression");
    return 0;
}

typedef struct {
    GMainContext *context;
    GMainLoop *loop;
    GSource *delivery;
    GThread *main_thread;
    GMutex mutex;
    gint counter;
    int value, received, timed_out, correct_thread;
} ThreadState;

static gboolean delivered(gpointer data)
{
    ThreadState *state = data;
    g_mutex_lock(&state->mutex);
    state->received = state->value == 42;
    state->correct_thread = g_thread_self() == state->main_thread;
    g_mutex_unlock(&state->mutex);
    g_main_loop_quit(state->loop);
    return G_SOURCE_REMOVE;
}

static gboolean timed_out(gpointer data)
{
    ThreadState *state = data;
    state->timed_out = 1;
    g_main_loop_quit(state->loop);
    return G_SOURCE_REMOVE;
}

static gpointer worker(gpointer data)
{
    ThreadState *state = data;
    g_mutex_lock(&state->mutex);
    state->value = 42;
    g_mutex_unlock(&state->mutex);
    g_atomic_int_add(&state->counter, 1);
    g_source_attach(state->delivery, state->context);
    return GINT_TO_POINTER(42);
}

static int thread_checks(void)
{
    ThreadState state = {0};
    g_mutex_init(&state.mutex);
    state.context = g_main_context_new();
    state.loop = g_main_loop_new(state.context, FALSE);
    state.main_thread = g_thread_self();
    state.delivery = g_idle_source_new();
    g_source_set_callback(state.delivery, delivered, &state, NULL);
    GSource *timeout = g_timeout_source_new(5000);
    g_source_set_callback(timeout, timed_out, &state, NULL);
    g_source_attach(timeout, state.context);
    GError *error = NULL;
    GThread *thread = g_thread_try_new("wesnoth-probe", worker, &state, &error);
    gpointer result = NULL;
    if (thread) {
        g_main_loop_run(state.loop);
        result = g_thread_join(thread);
    }
    gboolean good = thread && !error && !state.timed_out && state.received && state.correct_thread &&
        GPOINTER_TO_INT(result) == 42 && g_atomic_int_get(&state.counter) == 1;
    g_clear_error(&error);
    g_source_destroy(state.delivery); g_source_unref(state.delivery);
    g_source_destroy(timeout); g_source_unref(timeout);
    g_main_loop_unref(state.loop); g_main_context_unref(state.context);
    g_mutex_clear(&state.mutex);
    if (!good) return fail("GLib thread handoff, mutex, atomics or main context");
    puts("PASS GLib threads and main context");
    return 0;
}

static int module_checks(void)
{
    if (!g_module_supported()) return fail("GModule support unavailable");
    GModule *module = g_module_open(NULL, G_MODULE_BIND_LAZY);
    if (!module) return fail("GModule main process open");
    gpointer symbol = NULL;
    gboolean good = g_module_symbol(module, "malloc", &symbol) && symbol;
    good = g_module_close(module) && good;
    if (!good) return fail("GModule process symbol lookup or close");
    puts("PASS GModule process symbols");
    return 0;
}

int main(void)
{
    puts("WESNOTH_ANDROID_GLIB version=1.19.26 abi=x86_64 min_api=23");
    if (GLIB_MAJOR_VERSION != 2 || GLIB_MINOR_VERSION != 82 || GLIB_MICRO_VERSION != 1 ||
        glib_major_version != 2 || glib_minor_version != 82 || glib_micro_version != 1 ||
        glib_check_version(2, 82, 1) != NULL)
        return fail("pinned GLib headers or runtime version differ");
    printf("GLib: %u.%u.%u; zlib headers: %s; system zlib runtime: %s\n",
        glib_major_version, glib_minor_version, glib_micro_version, ZLIB_VERSION, zlibVersion());
    if (unicode_checks() || variant_checks() || object_checks() || stream_checks() ||
        thread_checks() || module_checks()) return 1;
    puts("PASS wesnoth-android-glib");
    return 0;
}
