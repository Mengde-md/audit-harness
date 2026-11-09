"""Build an offline Docker archive of the linux/amd64 audit images."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "registry-1.docker.io"
AUTH = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{}:pull"
CDNS = {
    "production.cloudflare.docker.com",
    "production.cloudfront.docker.com",
    "docker-images-prod.6aa30f8b08e16409b46e0173d6de2f56.r2.cloudflarestorage.com",
}
PYTHON_IMAGES = tuple(("library/python", tag) for tag in ("3.10-slim", "3.11-slim", "3.12-slim"))
MANAGED_IMAGES = (("library/postgres", "16"), ("pgvector/pgvector", "pg16"), ("library/redis", "5.0.5"))
PLATFORM = {"os": "linux", "architecture": "amd64"}
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"
ACCEPT = ",".join((OCI_INDEX, OCI_MANIFEST, DOCKER_MANIFEST,
                   "application/vnd.docker.distribution.manifest.list.v2+json"))
REPORT = b"[curl-response]"


def curl_get(url, *, token=None, accept=None, output=None):
    for _ in range(4):
        target = urlsplit(url)
        if (target.scheme != "https" or target.port not in (None, 443)
                or target.username is not None or target.password is not None
                or target.hostname not in {REGISTRY, "auth.docker.io", *CDNS}):
            raise RuntimeError(f"Unexpected download endpoint: {target.hostname}")
        # Keep the bearer token and any signed CDN URL out of the process arguments.
        config = "url = " + json.dumps(url) + "\n"
        if token and target.hostname == REGISTRY:
            config += 'header = "Authorization: Bearer ' + token + '"\n'
        if accept:
            config += 'header = "Accept: ' + accept + '"\n'
        process = subprocess.run([
            "curl", "-q", "--silent", "--proto", "=https", "--tlsv1.2",
            "--connect-timeout", "15", "--max-time", "240", "--retry", "2",
            "--output", str(output) if output else "-",
            "--write-out", REPORT.decode() + "%{http_code}\t%header{docker-content-digest}\t%{redirect_url}",
            "--config", "-",
        ], input=config.encode(), capture_output=True)
        if process.returncode:
            raise RuntimeError(f"curl failed for {target.hostname} (exit {process.returncode})")
        body, marker, response = process.stdout.rpartition(REPORT)
        if not marker:
            raise RuntimeError("Missing curl response metadata")
        status, content_digest, redirect = response.decode().split("\t", 2)
        if status == "200":
            return body, content_digest
        if status not in {"301", "302", "303", "307", "308"} or not redirect:
            raise RuntimeError(f"HTTP {status} from {target.hostname}")
        if output is None:
            raise RuntimeError(f"Unexpected metadata redirect from {target.hostname}")
        token = None
        url = redirect
    raise RuntimeError("Too many blob redirects")


def sha256_name(digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Expected a SHA-256 registry digest")
    return digest[7:]


def blob_name(descriptor):
    return "blobs/sha256/" + sha256_name(descriptor["digest"])


def sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def get_manifest(repository, ref, token, descriptor=None):
    raw, content_digest = curl_get(f"https://{REGISTRY}/v2/{repository}/manifests/{ref}", token=token, accept=ACCEPT)
    digest = hashlib.sha256(raw).hexdigest()
    if sha256_name(content_digest) != digest:
        raise RuntimeError(f"Registry response digest mismatch for {repository}:{ref}")
    if descriptor and (sha256_name(descriptor["digest"]) != digest or descriptor["size"] != len(raw)):
        raise RuntimeError(f"Manifest descriptor mismatch for {repository}:{ref}")
    manifest = json.loads(raw)
    if manifest.get("schemaVersion") != 2:
        raise RuntimeError(f"Unexpected manifest schema for {repository}:{ref}")
    return raw, manifest, content_digest


def resolve(repository, tag, token, directory):
    name = f"docker.io/{repository}:{tag}"
    raw, manifest, digest = get_manifest(repository, tag, token)
    platform = PLATFORM
    if "manifests" in manifest:
        matches = [item for item in manifest["manifests"]
                   if all(item.get("platform", {}).get(key) == value for key, value in PLATFORM.items())]
        if len(matches) != 1 or matches[0]["mediaType"] not in {OCI_MANIFEST, DOCKER_MANIFEST}:
            raise RuntimeError(f"Expected one linux/amd64 image for {name}")
        selected = matches[0]
        raw, manifest, digest = get_manifest(repository, selected["digest"], token, selected)
        platform = selected["platform"]
    media_type = manifest.get("mediaType", DOCKER_MANIFEST)
    if media_type not in {OCI_MANIFEST, DOCKER_MANIFEST}:
        raise RuntimeError(f"Unsupported manifest type for {name}")
    entry = {"mediaType": media_type, "digest": digest, "size": len(raw), "platform": platform}
    (directory / blob_name(entry)).write_bytes(raw)
    entry["annotations"] = {"io.containerd.image.name": name, "org.opencontainers.image.ref.name": tag}
    layers = manifest["layers"]
    config = manifest["config"]
    legacy_entry = {"Config": blob_name(config), "RepoTags": [name],
                    "Layers": [blob_name(layer) for layer in layers]}
    print(f"Resolved {name} to {digest}", flush=True)
    return entry, legacy_entry, config, layers


def fetch_blob(repository, descriptor, token, directory):
    name = blob_name(descriptor)
    path = directory / name
    if path.is_file() and path.stat().st_size == descriptor["size"] and sha256_file(path) == path.name:
        return
    with TemporaryDirectory(dir=directory) as temporary:
        part = Path(temporary) / "blob"
        curl_get(f"https://{REGISTRY}/v2/{repository}/blobs/{descriptor['digest']}", token=token, output=part)
        if part.stat().st_size != descriptor["size"] or sha256_file(part) != path.name:
            raise RuntimeError(f"Blob verification failed: {descriptor['digest']}")
        part.replace(path)
    print(f"Verified {descriptor['digest']} ({descriptor['size']} bytes)", flush=True)


def tar_json(archive, name, value):
    data = json.dumps(value, separators=(",", ":")).encode()
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data))


def pull_token(repository):
    credentials = json.loads(curl_get(AUTH.format(repository))[0])
    token = credentials.get("token") or credentials.get("access_token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
        raise RuntimeError("Registry did not provide a valid pull token")
    return token


def verify_layers(config, layers, cache, verified):
    image = json.loads((cache / blob_name(config)).read_bytes())
    if any(image.get(key) != value for key, value in PLATFORM.items()):
        raise RuntimeError("Image config does not match linux/amd64")
    for layer, expected in zip(layers, image["rootfs"]["diff_ids"], strict=True):
        path = cache / blob_name(layer)
        if path not in verified:
            media_type = layer["mediaType"]
            if media_type.endswith(("+gzip", ".gzip")):
                with gzip.open(path, "rb") as stream:
                    verified[path] = hashlib.file_digest(stream, "sha256").hexdigest()
            elif media_type.endswith(".tar"):
                verified[path] = path.name
            else:
                raise RuntimeError(f"Unsupported layer media type {media_type}")
        if verified[path] != sha256_name(expected):
            raise RuntimeError(f"Uncompressed layer digest mismatch for {layer['digest']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--managed-services", action="store_true", help="package PostgreSQL, pgvector and Redis")
    options = parser.parse_args()
    requested = MANAGED_IMAGES if options.managed_services else PYTHON_IMAGES
    output_name = "managed-services-linux-amd64.tar" if options.managed_services else "python-linux-amd64.tar"
    staging = ROOT / ".docker-staging"
    staging.mkdir(exist_ok=True)
    cache = staging / "python-blobs"
    (cache / "blobs/sha256").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        repositories = sorted({repository for repository, _ in requested})
        tokens = dict(zip(repositories, pool.map(pull_token, repositories), strict=True))
        images = list(pool.map(lambda pair: resolve(*pair, tokens[pair[0]], cache), requested))
        blobs = {}
        for (repository, _), (_, _, config, layers) in zip(requested, images, strict=True):
            for blob in [config, *layers]:
                blobs.setdefault(blob_name(blob), (repository, blob))
        print(f"Fetching {len(blobs)} distinct config and layer blobs", flush=True)
        for _ in pool.map(lambda pair: fetch_blob(*pair, tokens[pair[0]], cache), blobs.values()):
            pass
    verified = {}
    for _, _, config, layers in images:
        verify_layers(config, layers, cache, verified)
    names = set(blobs) | {blob_name(entry) for entry, _, _, _ in images}
    output = staging / output_name
    with TemporaryDirectory(dir=staging) as temporary:
        part = Path(temporary) / output.name
        with tarfile.open(part, "w") as archive:
            tar_json(archive, "oci-layout", {"imageLayoutVersion": "1.0.0"})
            tar_json(archive, "index.json", {"schemaVersion": 2, "mediaType": OCI_INDEX,
                                           "manifests": [entry for entry, _, _, _ in images]})
            tar_json(archive, "manifest.json", [legacy for _, legacy, _, _ in images])
            for name in sorted(names):
                path = cache / name
                entry = tarfile.TarInfo(name)
                entry.size = path.stat().st_size
                with path.open("rb") as stream:
                    archive.addfile(entry, stream)
        part.replace(output)
    print(f"{output} ({output.stat().st_size} bytes), SHA-256 {sha256_file(output)}", flush=True)


if __name__ == "__main__":
    main()
