//! Research-only verifier for the pinned TLSNotary synthetic fixture.

#![allow(unexpected_cfgs)]

#[cfg(tlsn_insecure)]
compile_error!("insecure TLSNotary mode is forbidden");

#[cfg(test)]
mod tests {
    use super::*;
    const SENT: &[u8] = b"GET /formats/json HTTP/1.1\r\nHost: test-server.io\r\n\r\n";
    const BODY_SHA: &str = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a";

    #[test]
    fn exact_http_contract_accepts_only_expected_response() {
        let response =
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}";
        assert_eq!(check_http(SENT, response, BODY_SHA).unwrap(), 2);
        for changed in [
            "HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Encoding: gzip\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n[]",
        ] {
            assert!(check_http(SENT, changed.as_bytes(), BODY_SHA).is_err());
        }
        for changed in [
            b"GET /other HTTP/1.1\r\nHost: test-server.io\r\n\r\n".as_slice(),
            b"POST /formats/json HTTP/1.1\r\nHost: test-server.io\r\n\r\n".as_slice(),
            b"GET /formats/json HTTP/1.1\r\nHost: other.test\r\n\r\n".as_slice(),
        ] {
            assert!(check_http(changed, response, BODY_SHA).is_err());
        }
    }
}

use anyhow::{Context, Result, ensure};
use bincode::Options;
use clap::Parser;
use std::{
    fs::File,
    io::Read,
    path::PathBuf,
    time::{SystemTime, UNIX_EPOCH},
};
use tlsn::{
    attestation::{
        CryptoProvider,
        presentation::Presentation,
        signing::{KeyAlgId, VerifyingKey},
    },
    connection::TlsVersion,
    hash::HashAlgId,
    transcript::Transcript,
    verifier::ServerCertVerifier,
    webpki::{CertificateDer, RootCertStore},
};
use tlsn_formats::http::{Header, HttpTranscript};
use tlsn_server_fixture_certs::CA_CERT_DER;

#[derive(Parser)]
struct Args {
    #[clap(long)]
    presentation: PathBuf,
    #[clap(long)]
    notary_key: PathBuf,
    #[clap(long)]
    body_sha256: String,
    #[clap(long, default_value = "test-server.io")]
    server: String,
    #[clap(long, default_value_t = 300)]
    max_age_seconds: u64,
}

fn read_bounded(path: &PathBuf, max: usize) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)?
        .take((max + 1) as u64)
        .read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= max, "input exceeds research limit");
    Ok(bytes)
}

fn unique_header(headers: &[Header], name: &str) -> Result<Vec<u8>> {
    let values: Vec<_> = headers
        .iter()
        .filter(|header| header.name.as_str().eq_ignore_ascii_case(name))
        .collect();
    ensure!(values.len() == 1, "missing or duplicate required header");
    Ok(values[0].value.as_bytes().to_vec())
}

fn check_http(sent: &[u8], received: &[u8], expected_digest: &str) -> Result<usize> {
    let transcript = Transcript::new(sent.to_vec(), received.to_vec());
    let http = HttpTranscript::parse(&transcript)?;
    ensure!(
        http.requests.len() == 1 && http.responses.len() == 1,
        "single exchange required"
    );
    let request = &http.requests[0];
    ensure!(
        request.request.method.as_str() == "GET",
        "unexpected method"
    );
    ensure!(
        request.request.target.as_str() == "/formats/json",
        "unexpected target"
    );
    ensure!(
        unique_header(&request.headers, "host")? == b"test-server.io",
        "unexpected Host"
    );
    ensure!(request.body.is_none(), "GET body forbidden");
    let response = &http.responses[0];
    ensure!(
        response.status.code.as_str() == "200",
        "unexpected HTTP status"
    );
    ensure!(
        unique_header(&response.headers, "content-type")? == b"application/json",
        "unexpected media type"
    );
    for forbidden in ["transfer-encoding", "content-encoding"] {
        ensure!(
            !response
                .headers
                .iter()
                .any(|h| h.name.as_str().eq_ignore_ascii_case(forbidden)),
            "encoded body not supported by this research contract"
        );
    }
    let body = response
        .body
        .as_ref()
        .context("body required")?
        .content_data();
    let declared: usize =
        std::str::from_utf8(&unique_header(&response.headers, "content-length")?)?.parse()?;
    ensure!(declared == body.len(), "body length mismatch");
    let provider = CryptoProvider::default();
    let digest = provider.hash.get(&HashAlgId::SHA256)?.hash(&body);
    ensure!(
        hex::encode(digest.as_bytes()) == expected_digest,
        "body digest mismatch"
    );
    Ok(body.len())
}

fn main() -> Result<()> {
    let args = Args::parse();
    ensure!(
        args.body_sha256.len() == 64
            && args
                .body_sha256
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "lowercase SHA-256 required"
    );
    let limits = bincode::DefaultOptions::new()
        .with_fixint_encoding()
        .reject_trailing_bytes()
        .with_limit(2 * 1024 * 1024);
    let presentation: Presentation =
        limits.deserialize(&read_bounded(&args.presentation, 2 * 1024 * 1024)?)?;
    let expected: VerifyingKey = limits.deserialize(&read_bounded(&args.notary_key, 1024)?)?;
    ensure!(
        expected.alg == KeyAlgId::K256 && presentation.verifying_key() == &expected,
        "untrusted notary"
    );
    // This fixture CA is research-only; it is not a public WebPKI trust anchor.
    let roots = RootCertStore {
        roots: vec![CertificateDer(CA_CERT_DER.to_vec())],
    };
    let provider = CryptoProvider {
        cert: ServerCertVerifier::new(&roots)?,
        ..Default::default()
    };
    let output = presentation.verify(&provider)?;
    ensure!(
        output
            .server_name
            .context("server identity proof required")?
            .to_string()
            == args.server,
        "unexpected server identity"
    );
    ensure!(
        output.connection_info.version == TlsVersion::V1_2,
        "unexpected TLS version"
    );
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let time = output.connection_info.time;
    ensure!(
        time <= now.saturating_add(10) && now.saturating_sub(time) <= args.max_age_seconds,
        "session outside freshness policy"
    );
    let partial = output.transcript.context("transcript proof required")?;
    ensure!(
        partial.is_complete(),
        "full authenticated disclosure required for body digest"
    );
    let bytes = check_http(
        partial.sent_unsafe(),
        partial.received_unsafe(),
        &args.body_sha256,
    )?;
    println!(
        "{}",
        serde_json::json!({"research_only":true, "result":"SYNTHETIC_TLS_CONTRACT_PASS", "server":args.server, "http_status":200, "body_bytes":bytes, "body_sha256":args.body_sha256})
    );
    Ok(())
}
