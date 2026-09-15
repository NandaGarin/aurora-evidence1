"""Command line entry point for AURORA Evidence (Development 2).

Runs with the standard library only, so every command below works before any
dependency is installed:

    python -m aurora_evidence.cli validate-bundle FILE
    python -m aurora_evidence.cli retrieve FILE [-o OUT] [--corpus PATH]
    python -m aurora_evidence.cli corpus-stats [--corpus PATH]
    python -m aurora_evidence.cli search QUERY [--corpus PATH]
    python -m aurora_evidence.cli detect-text TEXT [--profile FILE] [--provider NAME]
    python -m aurora_evidence.cli capabilities [--profile FILE]
    python -m aurora_evidence.cli selftest

``selftest`` is the documented one-command smoke check: it runs the demo bundle
through the full pipeline and asserts the contract invariants that matter for
Dev 2, printing actual numbers rather than a bare "ok".
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from aurora_evidence.contract.canonical import text_sha256
from aurora_evidence.contract.validate import check_bundle
from aurora_evidence.forensics.interfaces import TextDetectionRequest
from aurora_evidence.forensics.registry import DetectorRegistry, load_profiles
from aurora_evidence.retrieval.pipeline import RetrievalSettings, execute
from aurora_evidence.version import PIPELINE_VERSION, SCHEMA_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "fixtures" / "corpus" / "demo_corpus.jsonl"
DEFAULT_PROFILE = REPO_ROOT / "configs" / "providers.demo.toml"
DEMO_BUNDLE = REPO_ROOT / "prompt-aurora" / "contoh" / "01_INPUT_RETRIEVAL.json"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _settings_from_profile(
    profile_path: Path | None,
    *,
    corpus: Path,
    mode: str,
    image_provider: str | None = None,
    text_provider: str | None = None,
) -> RetrievalSettings:
    """Build pipeline settings from a TOML profile, allowing CLI overrides."""
    data: dict[str, Any] = {}
    profiles: dict[str, dict[str, Any]] = {}
    if profile_path and profile_path.exists():
        with profile_path.open("rb") as handle:
            data = tomllib.load(handle)
        profiles = load_profiles(profile_path)

    selection = data.get("selection", {})
    retrieval = data.get("retrieval", {})
    return RetrievalSettings(
        mode=mode,
        corpus_path=corpus,
        per_query_limit=int(retrieval.get("per_query_limit", 10)),
        max_evidence=int(retrieval.get("max_evidence", 20)),
        budget_seconds=float(retrieval.get("budget_seconds", 45)),
        strict_temporal=bool(retrieval.get("strict_temporal", True)),
        per_group_cap=retrieval.get("per_group_cap", 2),
        mmr_lambda=float(retrieval.get("mmr_lambda", 0.7)),
        corpus_version=str(retrieval.get("corpus_version", "unknown")),
        image_detector_provider=image_provider or selection.get("image_detector", "none"),
        text_detector_provider=text_provider or selection.get("text_detector", "none"),
        detector_profiles=profiles,
        # Credentials come from the environment, never from the profile file.
        detector_credentials=_credentials_from_env(),
    )


def _credentials_from_env() -> dict[str, str]:
    """Read detector credentials from the environment by config key name."""
    import os

    mapping = {
        "api_user": os.environ.get("AURORA_SIGHTENGINE_API_USER", ""),
        "api_secret": os.environ.get("AURORA_SIGHTENGINE_API_SECRET", ""),
        "api_key": os.environ.get("AURORA_TEXT_DETECTOR_API_KEY", "")
        or os.environ.get("AURORA_GPTZERO_API_KEY", ""),
    }
    return {key: value for key, value in mapping.items() if value}


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_validate_bundle(args: argparse.Namespace) -> int:
    bundle = _load_json(Path(args.file))
    violations = check_bundle(bundle, require_analysis=args.require_analysis)
    if not violations:
        print(f"VALID  {args.file}  (schema {SCHEMA_VERSION})")
        return 0
    print(f"INVALID  {args.file}  — {len(violations)} pelanggaran:")
    for violation in violations:
        print(f"  [{violation.code}] {violation.path or '<root>'}: {violation.message}")
    return 1


def cmd_retrieve(args: argparse.Namespace) -> int:
    bundle = _load_json(Path(args.file))
    settings = _settings_from_profile(
        Path(args.profile) if args.profile else DEFAULT_PROFILE,
        corpus=Path(args.corpus),
        mode=bundle.get("mode", "demo"),
        image_provider=args.image_detector,
        text_provider=args.text_detector,
    )
    result = execute(bundle, settings)
    retrieval = result["retrieval"]

    print(f"run_id      : {retrieval['run']['run_id']}")
    print(f"mode/status : {retrieval['run']['mode']} / {retrieval['run']['status']}")
    print(f"atom_set_id : {retrieval['atom_set_id']}")
    print(f"evidence    : {len(retrieval['evidence_list'])}")
    print(f"signals     : {len(retrieval['forensic_signals'])}")
    print("\nprovider_status:")
    for entry in retrieval["provider_status"]:
        print(
            f"  {entry['provider']:<22} {entry['capability']:<20} {entry['status']:<13}"
            f" {(entry['message'] or '')[:60]}"
        )
    if retrieval["run"]["warnings"]:
        print("\nwarnings:")
        for warning in retrieval["run"]["warnings"]:
            print(f"  [{warning['code']}] {warning['message']}")
    print("\nevidence:")
    for evidence in retrieval["evidence_list"]:
        source, provenance = evidence["source"], evidence["provenance"]
        print(
            f"  {evidence['evidence_id']}  rel={_fmt(evidence['relevance_score'])}"
            f" rerank={_fmt(evidence['rerank_score'])} cred={_fmt(evidence['credibility_score'])}"
            f" temporal={provenance['temporal_eligible']}"
        )
        print(f"     [{source['kind']}] {source['title']}  — {source['publisher'] or 'tanpa publisher'}")
        print(f"     url={source['url']}  status={evidence['content']['status']}")
        print(f"     kutipan: {evidence['content']['excerpt'][:120]!r}")
    print("\nforensic_signals (indikasi asal konten, BUKAN label hoaks):")
    for signal in retrieval["forensic_signals"]:
        print(
            f"  {signal['provider']:<20} {signal['modality']:<6} status={signal['status']:<12}"
            f" assessment={signal['assessment']:<22} raw={signal['raw_score']}"
            f" ai={signal['ai_generated_score']}"
        )
        for limitation in signal["limitations"]:
            print(f"     - {limitation}")

    if args.output:
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nbundle ditulis ke {args.output}")
    return 0


def _fmt(value: float | None) -> str:
    return "null" if value is None else f"{value:.4f}"


def cmd_corpus_stats(args: argparse.Namespace) -> int:
    from aurora_evidence.retrieval.providers.local_corpus import LocalCorpusProvider

    provider = LocalCorpusProvider(Path(args.corpus))
    provider.load()
    capability = provider.capability()
    print(f"corpus   : {args.corpus}")
    print(f"status   : {capability.status} — {capability.message}")
    kinds: dict[str, int] = {}
    undated = 0
    no_url = 0
    for document in provider.docs:
        kinds[document.get("kind", "?")] = kinds.get(document.get("kind", "?"), 0) + 1
        undated += 1 if not document.get("published_at") else 0
        no_url += 1 if not document.get("url") else 0
    print(f"documents: {len(provider.docs)}  (tanpa tanggal: {undated}, tanpa URL: {no_url})")
    for kind, count in sorted(kinds.items()):
        print(f"  {kind:<16} {count}")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from aurora_evidence.retrieval.providers.local_corpus import LocalCorpusProvider

    provider = LocalCorpusProvider(Path(args.corpus))
    provider.load()
    hits = provider.search(args.query, limit=args.limit)
    if not hits:
        print("Tidak ada dokumen yang cocok (BM25 skor <= 0).")
        return 0
    print(f"{len(hits)} hasil BM25 untuk {args.query!r}:")
    for hit in hits:
        print(f"  {hit.provider_score:7.3f}  {hit.title}")
        print(f"           {hit.url}  [{hit.kind}] {hit.publisher}")
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile) if args.profile else DEFAULT_PROFILE
    profiles = load_profiles(profile_path) if profile_path.exists() else {}
    data: dict[str, Any] = {}
    if profile_path.exists():
        with profile_path.open("rb") as handle:
            data = tomllib.load(handle)
    selection = data.get("selection", {})
    registry = DetectorRegistry(
        profiles=profiles, credentials=_credentials_from_env(), mode=args.mode
    )
    print(f"profil : {profile_path}")
    print(f"mode   : {args.mode}")
    for capability in registry.capabilities(
        image_provider=args.image_detector or selection.get("image_detector", "none"),
        text_provider=args.text_detector or selection.get("text_detector", "none"),
    ):
        print(f"\n  provider   : {capability.provider}")
        print(f"  capability : {capability.capability}")
        print(f"  status     : {capability.status}")
        print(f"  fixture    : {capability.is_fixture}")
        print(f"  bahasa     : {list(capability.languages) or 'tidak didokumentasikan'}")
        print(f"  butuh cfg  : {list(capability.required_config) or '-'}")
        print(f"  pesan      : {capability.message}")
        for limitation in capability.limitations:
            print(f"    - {limitation}")
    return 0


def cmd_detect_text(args: argparse.Namespace) -> int:
    profile_path = Path(args.profile) if args.profile else DEFAULT_PROFILE
    profiles = load_profiles(profile_path) if profile_path.exists() else {}
    registry = DetectorRegistry(
        profiles=profiles, credentials=_credentials_from_env(), mode=args.mode
    )
    detector = registry.text_detector(args.provider)
    capability = detector.capability()
    print(f"provider: {capability.provider}  status={capability.status}")
    if capability.status != "ok":
        print(f"  {capability.message}")
        print("  -> tidak dipanggil; tidak ada fallback fixture.")
        return 0
    outcome = detector.detect(
        TextDetectionRequest(
            text=args.text,
            text_sha256=text_sha256(args.text),
            target_kind="claim_text",
            language=args.language,
        )
    )
    print(f"  status     : {outcome.status}")
    print(f"  raw_score  : {outcome.raw_score}")
    print(f"  raw_scale  : {outcome.raw_scale.as_dict() if outcome.raw_scale else None}")
    print(f"  error_code : {outcome.error_code}")
    for limitation in outcome.limitations:
        print(f"    - {limitation}")
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    """One-command smoke check with real assertions and printed numbers."""
    print(f"AURORA Evidence selftest — pipeline {PIPELINE_VERSION}, schema {SCHEMA_VERSION}")
    bundle = _load_json(DEMO_BUNDLE)
    checks: list[tuple[str, bool, str]] = []

    violations = check_bundle(bundle)
    checks.append(("contoh input demo valid terhadap kontrak", not violations, str(violations[:2])))

    settings = _settings_from_profile(
        DEFAULT_PROFILE,
        corpus=Path(args.corpus),
        mode="demo",
        image_provider="fixture",
        text_provider="fixture",
    )
    result = execute(bundle, settings)
    retrieval = result["retrieval"]
    evidence = retrieval["evidence_list"]

    checks.append(("output bundle valid terhadap kontrak", not check_bundle(result), ""))
    checks.append(("ada bukti dari corpus lokal nyata", len(evidence) > 0, f"{len(evidence)} bukti"))
    checks.append(
        (
            "atom_set_id null tanpa analysis (inv 2)",
            retrieval["atom_set_id"] is None,
            str(retrieval["atom_set_id"]),
        )
    )
    checks.append(
        (
            "semua atom_ids kosong tanpa analysis (inv 2)",
            all(not item["atom_ids"] for item in evidence),
            "",
        )
    )
    checks.append(
        (
            "excerpt selalu substring content.text (inv 7)",
            all(item["content"]["excerpt"] in item["content"]["text"] for item in evidence),
            "",
        )
    )
    checks.append(
        (
            "content.sha256 == sha256(UTF-8 text) (inv 7)",
            all(
                item["content"]["sha256"] == text_sha256(item["content"]["text"])
                for item in evidence
            ),
            "",
        )
    )
    groups = {item["provenance"]["independence_group_id"] for item in evidence}
    checks.append(
        ("dedup menghasilkan >1 kelompok independen", len(groups) > 1, f"{len(groups)} grup")
    )
    syndicated = [
        item
        for item in evidence
        if item["source"]["publisher"] in {"Kantor Berita Demo", "Portal Demo"}
    ]
    checks.append(
        (
            "sindikasi lintas domain -> satu independence group (inv 8)",
            len({item["provenance"]["independence_group_id"] for item in syndicated}) == 1,
            f"{len(syndicated)} salinan",
        )
    )
    undated = [item for item in evidence if item["provenance"]["temporal_eligible"] is None]
    checks.append(("tanggal tak diketahui -> temporal_eligible null", bool(undated), f"{len(undated)} bukti"))
    future = [item for item in evidence if item["provenance"]["temporal_eligible"] is False]
    checks.append(("bukti setelah as_of -> false, tetap tampil", bool(future), f"{len(future)} bukti"))
    checks.append(
        (
            "detector terpisah dari evidence_list (inv 6)",
            all(item["source"]["kind"] != "ai_detection" for item in evidence),
            f"{len(retrieval['forensic_signals'])} signal",
        )
    )
    short_text_signal = [
        signal
        for signal in retrieval["forensic_signals"]
        if signal["status"] == "unsupported" and signal["ai_generated_score"] is None
    ]
    checks.append(
        (
            "teks pendek/bahasa tak didukung -> unsupported + skor null (inv 15)",
            bool(short_text_signal),
            "",
        )
    )
    checks.append(
        (
            "tidak ada signal 'likely_ai_generated' saat status != ok (inv 11)",
            all(
                not (signal["status"] != "ok" and signal["assessment"] == "likely_ai_generated")
                for signal in retrieval["forensic_signals"]
            ),
            "",
        )
    )

    print()
    failed = 0
    for label, ok, detail in checks:
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    stats = result["extensions"]["aurora_evidence"]["last_run"]
    print(f"\n  dedup    : {stats['dedup']}")
    print(f"  diversity: {stats['diversity']}")
    print(f"\n{len(checks) - failed}/{len(checks)} pemeriksaan lulus")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aurora-evidence", description="AURORA Development 2 — retrieval bukti & AI detector"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-bundle", help="validasi AuroraBundle terhadap kontrak")
    validate.add_argument("file")
    validate.add_argument(
        "--require-analysis",
        action="store_true",
        help="wajibkan analysis (perilaku Dev 3), default Dev 2 mengizinkan null",
    )
    validate.set_defaults(func=cmd_validate_bundle)

    retrieve = sub.add_parser("retrieve", help="jalankan pipeline retrieval atas sebuah bundle")
    retrieve.add_argument("file")
    retrieve.add_argument("-o", "--output")
    retrieve.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    retrieve.add_argument("--profile")
    retrieve.add_argument("--image-detector")
    retrieve.add_argument("--text-detector")
    retrieve.set_defaults(func=cmd_retrieve)

    stats = sub.add_parser("corpus-stats", help="ringkasan corpus lokal")
    stats.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    stats.set_defaults(func=cmd_corpus_stats)

    search = sub.add_parser("search", help="query BM25 langsung ke corpus lokal")
    search.add_argument("query")
    search.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    search.add_argument("--limit", type=int, default=5)
    search.set_defaults(func=cmd_search)

    caps = sub.add_parser("capabilities", help="status capability detector")
    caps.add_argument("--profile")
    caps.add_argument("--mode", default="demo", choices=["demo", "live"])
    caps.add_argument("--image-detector")
    caps.add_argument("--text-detector")
    caps.set_defaults(func=cmd_capabilities)

    detect = sub.add_parser("detect-text", help="jalankan detector teks atas sebuah string")
    detect.add_argument("text")
    detect.add_argument("--provider", default="fixture")
    detect.add_argument("--profile")
    detect.add_argument("--mode", default="demo", choices=["demo", "live"])
    detect.add_argument("--language", default="id")
    detect.set_defaults(func=cmd_detect_text)

    selftest = sub.add_parser("selftest", help="smoke check satu perintah dengan asersi nyata")
    selftest.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
