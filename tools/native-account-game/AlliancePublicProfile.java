package org.wesnoth.offline;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

/** Strict, bounded parsing of a public connection profile. No sockets or trust-store changes. */
final class AlliancePublicProfile {
    static final int MAX_BYTES = 32768;
    final byte[] bytes;
    final String address;
    final int port;
    final String fingerprint;

    private AlliancePublicProfile(byte[] data, String host, int endpoint, String pin) {
        bytes = data.clone(); address = host; port = endpoint; fingerprint = pin;
    }
    static AlliancePublicProfile parse(byte[] data) throws IOException {
        try {
            if (data.length == 0 || data.length > MAX_BYTES) throw new IOException("Profile must contain at most 32 KiB.");
            String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(data)).toString();
            Map<String, Object> fields = new Parser(text).object();
            if (!fields.keySet().equals(new HashSet<>(Arrays.asList("format", "protocol", "server_address", "port",
                    "minimum_tls", "certificate_pem", "certificate_sha256")))) throw new IOException("Unexpected profile fields.");
            if (!Integer.valueOf(1).equals(fields.get("format")) || !"wesnoth-alliance-tls".equals(fields.get("protocol"))
                    || !"1.2".equals(fields.get("minimum_tls"))) throw new IOException("Unsupported profile format.");
            String host = string(fields, "server_address");
            // This first account handoff deliberately accepts canonical IPv4 only.
            // No InetAddress lookup: a profile can never cause DNS resolution here.
            String[] parts = host.split("\\.", -1);
            if (parts.length != 4) throw new IOException("Use the server's numeric IPv4 profile.");
            int[] octets = new int[4];
            for (int index = 0; index < 4; index++) {
                if (!parts[index].matches("0|[1-9][0-9]{0,2}")) throw new IOException("Noncanonical server address.");
                octets[index] = Integer.parseInt(parts[index]);
                if (octets[index] > 255) throw new IOException("Invalid server address.");
            }
            if (octets[0] == 0 || octets[0] >= 224 || host.equals("255.255.255.255")) throw new IOException("Invalid server address.");
            Object endpoint = fields.get("port");
            if (!(endpoint instanceof Integer) || (Integer) endpoint < 1 || (Integer) endpoint > 65535) throw new IOException("Invalid server port.");
            String pem = string(fields, "certificate_pem");
            if (pem.length() > 16384 || !pem.matches("(?s)-----BEGIN CERTIFICATE-----\\n[A-Za-z0-9+/=\\r\\n]+-----END CERTIFICATE-----\\n")) {
                throw new IOException("Profile must contain one public certificate.");
            }
            String pin = string(fields, "certificate_sha256");
            if (!pin.matches("[0-9a-f]{64}")) throw new IOException("Invalid certificate fingerprint.");
            ByteArrayInputStream input = new ByteArrayInputStream(pem.getBytes(StandardCharsets.US_ASCII));
            X509Certificate certificate = (X509Certificate) CertificateFactory.getInstance("X.509").generateCertificate(input);
            certificate.checkValidity();
            StringBuilder digest = new StringBuilder();
            for (byte value : MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded())) digest.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            if (!pin.equals(digest.toString())) throw new IOException("Certificate fingerprint does not match.");
            Collection<List<?>> names = certificate.getSubjectAlternativeNames();
            boolean matches = false;
            if (names != null) for (List<?> entry : names) {
                if (entry.size() == 2 && Integer.valueOf(7).equals(entry.get(0)) && host.equals(entry.get(1))) matches = true;
            }
            if (!matches) throw new IOException("Certificate does not identify this server address.");
            return new AlliancePublicProfile(data, host, (Integer) endpoint, pin);
        } catch (IOException error) { throw error; }
        catch (Exception error) { throw new IOException("The public profile or certificate is invalid.", error); }
    }
    private static String string(Map<String, Object> fields, String key) throws IOException {
        Object value = fields.get(key);
        if (!(value instanceof String)) throw new IOException("Invalid profile field type.");
        return (String) value;
    }

    /** Flat JSON only; rejects duplicate keys, floats, nested values and trailing bytes. */
    private static final class Parser {
        private final String text; private int offset;
        Parser(String value) { text = value; }
        private void space() { while (offset < text.length() && " \t\r\n".indexOf(text.charAt(offset)) >= 0) offset++; }
        private void expect(char value) throws IOException {
            space(); if (offset >= text.length() || text.charAt(offset++) != value) throw new IOException("Invalid profile JSON.");
        }
        Map<String, Object> object() throws IOException {
            Map<String, Object> result = new HashMap<>(); expect('{'); space();
            if (offset < text.length() && text.charAt(offset) == '}') { offset++; }
            else while (true) {
                String key = quoted(); expect(':'); space(); Object value;
                if (offset < text.length() && text.charAt(offset) == '"') value = quoted();
                else {
                    int start = offset;
                    while (offset < text.length() && text.charAt(offset) >= '0' && text.charAt(offset) <= '9') offset++;
                    String integer = text.substring(start, offset);
                    if (!integer.matches("0|[1-9][0-9]{0,8}")) throw new IOException("Invalid profile integer.");
                    value = Integer.valueOf(integer);
                }
                if (result.containsKey(key) || result.size() >= 7) throw new IOException("Duplicate or excessive profile fields.");
                result.put(key, value); space();
                if (offset < text.length() && text.charAt(offset) == '}') { offset++; break; }
                expect(',');
            }
            space(); if (offset != text.length()) throw new IOException("Unexpected profile data.");
            return result;
        }
        private String quoted() throws IOException {
            expect('"'); StringBuilder value = new StringBuilder();
            while (offset < text.length()) {
                char next = text.charAt(offset++);
                if (next == '"') return value.toString();
                if (next < 32) throw new IOException("Control character in profile JSON.");
                if (next == '\\') {
                    if (offset == text.length()) throw new IOException("Incomplete JSON escape.");
                    next = text.charAt(offset++);
                    switch (next) {
                        case '"': case '\\': case '/': value.append(next); break;
                        case 'b': value.append('\b'); break; case 'f': value.append('\f'); break;
                        case 'n': value.append('\n'); break; case 'r': value.append('\r'); break; case 't': value.append('\t'); break;
                        case 'u':
                            if (offset + 4 > text.length() || !text.substring(offset, offset + 4).matches("[0-9a-fA-F]{4}")) throw new IOException("Invalid JSON escape.");
                            char unicode = (char) Integer.parseInt(text.substring(offset, offset + 4), 16); offset += 4;
                            if (Character.isSurrogate(unicode)) throw new IOException("Unsupported surrogate in profile JSON.");
                            value.append(unicode); break;
                        default: throw new IOException("Invalid JSON escape.");
                    }
                } else {
                    if (Character.isSurrogate(next)) throw new IOException("Unsupported surrogate in profile JSON.");
                    value.append(next);
                }
            }
            throw new IOException("Unclosed profile string.");
        }
    }
}
