# Kontrak integrasi AURORA v1.0.0 — wajib identik pada ketiga development

Bagian ini adalah spesifikasi bersama. Implementasikan sebagai model Pydantic, JSON Schema, OpenAPI, tipe TypeScript, dan contract tests. Salinannya di ketiga prompt harus identik. Jangan mengganti nama, arti, tipe, atau enum field publik secara sepihak. Penambahan internal masuk ke `extensions`; perubahan kontrak memerlukan versi baru dan migration adapter. Development 1 memelihara definisi atom, development 2 definisi evidence/forensics, development 3 definisi decision dan pengujian integrasi.

## Bentuk data normatif

Semua field wajib hadir kecuali ditandai `?`. Nullable berarti `null`, bukan string kosong, angka nol, atau array kosong sebagai pengganti informasi yang tidak diketahui.

```typescript
type Score = number; // bilangan finite dalam [0,1]; bukan otomatis probabilitas
type ISODateTime = string; // RFC 3339 dengan timezone; simpan UTC
type VisualLabel = "Supported" | "Contradicted" | "Unobservable";
type FactLabel = "Supported" | "Contradicted" | "InsufficientEvidence";
type Role = "actor" | "action" | "object" | "attribute" | "location"
          | "time" | "quantity" | "relation" | "cause";
type Mode = "demo" | "live";
type Warning = { code: string; message: string; component: string };

interface MediaRef {
  asset_id: string; sha256: string; media_type: string;
  width: number; height: number; uri: string;
}
interface RunInfo {
  run_id: string; mode: Mode; started_at: ISODateTime;
  finished_at: ISODateTime; status: "completed" | "partial" | "failed";
  versions: Record<string,string>; warnings: Warning[];
}
interface Atom {
  atom_id: string; statement: string; role: Role;
  subject: string | null; predicate: string; object: string | null;
  qualifiers: {
    negated: boolean; quantity: number | null;
    time: string | null; location: string | null;
  };
  spans: { start: number; end: number }[];
  depends_on: string[]; check_worthiness: Score;
  parser_confidence: Score | null;
}
interface Region {
  region_id: string; asset_id: string;
  bbox: [number, number, number, number]; // x_min,y_min,x_max,y_max
  score: Score | null; description: string;
}
interface VisualAssessment {
  atom_id: string; visual_status: VisualLabel;
  probabilities: Record<VisualLabel,number> | null;
  unmatched_mass: Score | null; observability_score: Score | null;
  supporting_regions: Region[]; contradicting_regions: Region[];
  counter_evidence: string | null; rationale: string;
  inference_kind: "trained" | "heuristic" | "fixture" | "unavailable";
}
interface Analysis {
  run: RunInfo; atom_set_id: string; atomic_claims: Atom[];
  visual_assessments: VisualAssessment[];
  ocr: { text: string; bbox: [number,number,number,number];
         confidence: Score | null; language: string | null }[];
}
interface Evidence {
  evidence_id: string; atom_ids: string[];
  source: {
    provider: string;
    kind: "fact_check" | "news" | "official" | "web"
        | "image_provenance" | "local_corpus" | "user_supplied";
    url: string | null; title: string; publisher: string | null;
    language: string | null; published_at: ISODateTime | null;
    retrieved_at: ISODateTime;
  };
  content: {
    text: string; excerpt: string; sha256: string;
    status: "full" | "excerpt_only" | "snippet_only" | "unavailable";
  };
  provenance: {
    original_url: string | null; archive_url: string | null;
    first_seen_at: ISODateTime | null; captured_at: ISODateTime | null;
    date_basis: string | null; discovery_method: string;
    duplicate_cluster_id: string; independence_group_id: string;
    temporal_eligible: boolean | null; usage_note: string | null;
    image_matches: { asset_id: string; matched_url: string | null;
      match_type: "exact" | "near_duplicate" | "semantic";
      score: Score | null }[];
  };
  relevance_score: Score | null; credibility_score: Score | null;
  rerank_score: number | null; score_breakdown: Record<string,number>;
}
interface ForensicSignal {
  signal_id: string;
  target: { kind: "claim_text" | "image" | "ocr_text";
            asset_id: string | null; text_sha256: string | null };
  modality: "image" | "text"; provider: string; model_version: string | null;
  task: "ai_generation_detection";
  status: "ok" | "inconclusive" | "unsupported" | "unavailable" | "failed";
  raw_score: number | null;
  raw_scale: { min: number; max: number; higher_means_ai: boolean } | null;
  ai_generated_score: Score | null; raw_label: string | null;
  assessment: "likely_ai_generated" | "likely_human_or_camera"
            | "uncertain" | "not_assessed";
  calibration_status: "unknown" | "provider_claimed"
                    | "locally_validated" | "not_applicable";
  applicable_language: string | null; limitations: string[];
  analyzed_at: ISODateTime; error_code: string | null;
}
interface Retrieval {
  run: RunInfo; atom_set_id: string | null;
  evidence_list: Evidence[]; forensic_signals: ForensicSignal[];
  query_log: { query_id: string; atom_ids: string[]; provider: string;
               query: string; duration_ms: number; result_count: number }[];
  provider_status: { provider: string; capability: string;
    status: "ok" | "disabled" | "unconfigured" | "rate_limited"
          | "failed" | "unsupported"; message: string | null }[];
}
interface EvidenceLink {
  evidence_id: string; atom_id: string;
  stance: "Supports" | "Contradicts" | "NotRelevant" | "Unclear";
  quote: string | null; rationale: string;
}
interface Calibration {
  status: "calibrated" | "uncalibrated" | "demo_only";
  calibration_id: string | null; method: string | null;
  alpha: number | null; sample_count: number; group: string | null;
  fallback_used: string | null; validity_notes: string[];
}
interface AtomicDecision {
  atom_id: string; base_label: FactLabel; status: FactLabel;
  probabilities: Record<FactLabel,number> | null;
  confidence_set: FactLabel[] | null;
  abstention_flag: boolean; abstention_reasons: string[];
  evidence_links: EvidenceLink[]; visual_atom_ids: string[];
  explanation: string; calibration: Calibration;
}
interface Decision {
  run: RunInfo; atom_set_id: string;
  atomic_verdicts: AtomicDecision[];
  base_label: FactLabel; final_verdict: FactLabel;
  probabilities: Record<FactLabel,number> | null;
  confidence_set: FactLabel[] | null;
  abstention_flag: boolean; abstention_reasons: string[];
  calibration: Calibration;
  decision_report: {
    summary: string; key_findings: string[]; unresolved_questions: string[];
    evidence_ids: string[]; forensic_signal_ids: string[];
    limitations: string[]; suggested_next_steps: string[];
    misinformation_category: string | null;
  };
  human_review: {
    reviewer: string; reviewed_at: ISODateTime;
    verdict: FactLabel; reason: string;
  } | null;
}
interface AuroraBundle {
  schema_version: "1.0.0"; case_id: string; claim_revision: number;
  mode: Mode; created_at: ISODateTime;
  input: {
    claim_text: string; language: string; image: MediaRef | null;
    as_of: ISODateTime | null;
  };
  analysis: Analysis | null; retrieval: Retrieval | null;
  decision: Decision | null;
  warnings: Warning[]; extensions: Record<string,unknown>;
}
```

## Invarian yang harus diuji

1. Case ID tetap sepanjang pipeline. Caption atau byte gambar berubah: `claim_revision` bertambah, hasil turunan menjadi stale. URI transport boleh berubah tanpa mengubah identitas gambar. `analysis.atom_set_id` adalah hash deterministik atas revisi dan daftar atom kanonis. Atom ID unik dan stabil pada atom set yang sama. Spans memakai indeks Unicode code point pada caption asli, end eksklusif; jangan mencampurnya dengan indeks UTF-16 frontend. `depends_on` tidak boleh dangling/cyclic.
2. Development 2 boleh berjalan dengan `analysis=null` dan `atom_ids=[]`; ia menggunakan caption utuh sebagai query. Jika analysis tersedia, `atom_set_id` dan referensi atom harus sama. Development 3 memerlukan analysis yang valid dari modul 1 atau impor standar; jika tidak ada, kembalikan `INPUT_ANALYSIS_REQUIRED`, jangan membuat atom diam-diam.
3. `probabilities` berisi semua label yang sesuai, setiap nilai finite `>=0` dan jumlahnya 1 dalam toleransi 1e-6. Skor similarity, ranking, residual UOT, parser, atau detector tidak boleh dinamai probabilitas terkalibrasi. Unknown/null tidak boleh diganti 0.
4. Bbox relatif terhadap gambar setelah EXIF orientation diterapkan: `0 <= x_min < x_max <= 1` dan `0 <= y_min < y_max <= 1`. SHA-256 identitas memakai byte unggahan asli.
5. Unobservable hanya berarti tidak dapat dinilai dari gambar. Ia tidak sama dengan Contradicted, tidak otomatis menjadi InsufficientEvidence setelah bukti eksternal tersedia, dan tidak sama dengan model belum dijalankan.
6. `evidence_list` berisi sumber bukti faktual/provenance yang benar-benar diperoleh. AI detector hanya masuk `forensic_signals`. Angka detektor tidak menjadi stance, kebenaran klaim, atau voting tambahan. Keaslian kamera/manusia juga bukan bukti bahwa caption benar.
7. `source.url` boleh null untuk dokumen lokal/impor dengan provenance jelas. Jangan mengarang URL, judul, kutipan, tanggal, atau skor. `excerpt` harus substring dari `content.text`, kecuali content kosong yang mengharuskan excerpt kosong. `content.sha256` menghitung UTF-8 `content.text` persis. Tanggal publikasi/first seen bukan tanggal foto diambil. Waktu yang tidak diketahui tetap null.
8. `atom_ids` dan `evidence_links` harus merujuk objek yang benar pada bundle/revisi yang sama. Bukti duplikat/sindikasi memakai `independence_group_id` yang sama meskipun URL berbeda. Mutasi modul 1 mengosongkan retrieval dan decision yang stale; mutasi modul 2 mengosongkan decision. Modul 3 mempertahankan input, analysis, retrieval.
9. `confidence_set=null` berarti kalibrasi yang sesuai tidak tersedia. `confidence_set=[]` berarti himpunan kosong hasil metode yang benar-benar dihitung. AtomicDecision memakai label faktual S/C/IE, bukan label visual S/C/U.
10. Jika `abstention_flag=true`, status/final_verdict operasional adalah InsufficientEvidence; base_label dan confidence_set asli tetap disimpan untuk audit. Human review tidak menimpa output model.
11. `ForensicSignal.status` selain `ok` tidak boleh menghasilkan tuduhan `likely_ai_generated`. `ai_generated_score` hanya diisi bila arah/skala asli diketahui. Target image memakai `asset_id`; target teks memakai hash teks persis.
12. Demo dan live tidak tercampur diam-diam. Semua `run.mode` harus sesuai `bundle.mode`. Demo/fixture diberi label terlihat dan dilarang masuk evaluasi confirmatory. Versi lain menghasilkan `SCHEMA_VERSION_UNSUPPORTED`.
13. `input.claim_text` wajib nonkosong setelah trim, tetapi teks asli disimpan tanpa trim/normalisasi diam-diam; `language` memakai tag BCP 47 (`id`/`en`/`und`). `claim_revision` integer `>=1`; dimensi gambar integer positif; `duration_ms`/jumlah hasil tidak negatif. Modul 2 mengizinkan `image=null`.
14. AtomicDecision tepat satu per atom pada keputusan completed. EvidenceLink `quote` non-null wajib substring persis `content.text`. `confidence_set` tidak mengandung label duplikat.
15. Target forensic image: `modality=image`, `asset_id` ada, `text_sha256=null`. Target `claim_text`/`ocr_text`: `modality=text`, `asset_id=null`, hash teks UTF-8 tepat. Status `unsupported`/`unavailable`/`failed` memakai `assessment=not_assessed` dan `ai_generated_score=null`; `inconclusive` memakai `assessment=uncertain`. `raw_score` harus finite jika ada, `raw_scale.min < raw_scale.max`, normalisasi di luar skala menghasilkan warning/error bukan clamp diam-diam.
16. Tanpa kalibrasi cocok: `calibration.status=uncalibrated`, `confidence_set=null`, `alpha=null`, `calibration_id=null`, `sample_count=0`.

## Identitas dan kanonisasi yang sama di tiga repo

Hash adalah SHA-256 hex lowercase 64 karakter. JSON kanonis mengikuti RFC 8785/JCS, UTF-8 tanpa BOM, menolak NaN/Infinity dan duplicate keys; gunakan library yang sesuai serta golden vector lintas Python/TypeScript. Tidak ada Unicode normalization pada caption atau isi kutipan.

| Objek | Aturan normatif |
| --- | --- |
| case_id, run_id, job_id | UUID lowercase dengan tanda hubung; pertahankan case_id ketika dipertukarkan. |
| asset_id | `asset_` + sha256 byte unggahan asli; sama di semua layanan. URI tidak masuk identitas. |
| atom_id | `a000001`, dst. dalam satu atom set. Urutkan menurut span start/end, lalu role dan statement lexicographic code point. |
| atom_set_id | `aset_` + SHA256(JCS objek `{case_id, claim_revision, claim_text_sha256, image_sha256, atomic_claims}`). |
| evidence_id | `ev_` + UUID run retrieval tanpa tanda hubung + `_` + urutan enam digit. Retry job sama tidak membuat ID baru. |
| signal_id | `fs_` + UUID run retrieval tanpa tanda hubung + `_` + urutan enam digit. |
| region_id | `rg_` + UUID run analysis tanpa tanda hubung + `_` + urutan enam digit. |
| duplicate_cluster_id, independence_group_id | String opaque nonkosong stabil dalam snapshot corpus/run. ID berbeda bukan bukti independensi. Impor mempertahankannya. |

Retrieval dengan `analysis=null` memakai `atom_set_id=null` dan semua `atom_ids=[]`.

## API, pertukaran berkas, dan kepemilikan integrasi

Semua modul menyediakan `GET /health` (liveness), `GET /ready` (database/worker + capability readiness), `POST /api/v1/media` (upload multipart image), `GET /api/v1/media/{asset_id}` (resolve asset), `GET /api/v1/jobs/{job_id}`.

- Development 1: `POST /api/v1/analyze` (port API 8101, frontend 5171).
- Development 2: `POST /api/v1/retrieve` (port API 8102, frontend 5172).
- Development 3: `POST /api/v1/fuse` + `POST /api/v1/pipeline` (port API 8103, frontend 5173).

Endpoint proses menerima AuroraBundle, memvalidasi, lalu mengembalikan HTTP 202 `{job_id, case_id, claim_revision, status:"queued"}`. Polling job mengembalikan `{job_id, status, result, error}` dengan status `queued/running/succeeded/partial/failed`, result adalah AuroraBundle atau null, error `{code,message,retryable}` atau null.

Header `Idempotency-Key` wajib pada endpoint proses; key sama + payload sama -> job sama; key sama + payload beda -> 409. Validasi 422 untuk payload salah, 409 untuk revisi/atom set tidak cocok. Hash payload memakai JCS request sebelum modifikasi server.

`POST /api/v1/retrieve` mempertahankan input/analysis, mengganti retrieval, dan mengosongkan decision. Parent run IDs dan hash snapshot input dicatat di `extensions.aurora_contract.run_inputs` per run_id.

`GET /health` mengembalikan `{status:"ok", service, schema_version:"1.0.0"}`. `GET /ready` mengembalikan `ready/degraded/not_ready` beserta database, worker, dan capabilities per provider/mode. Dependensi lokal wajib gagal: HTTP 503; provider opsional belum terkonfigurasi: HTTP 200 degraded + capability `unconfigured`. Error API memakai `{error:{code,message,retryable,details}}`. `INPUT_ANALYSIS_REQUIRED`/`SCHEMA_VERSION_UNSUPPORTED` memakai 422; konflik revisi/atom/idempotency memakai 409.

Ekspor ZIP minimal memuat `bundle.json` dan `media/{asset_id}.{ext}` bila image tidak null; `MediaRef.uri` dalam bundle ekspor adalah path relatif tersebut. Tolak absolute path, `..`, symlink, duplicate archive entry, decompression bomb, dan file di luar direktori ekstraksi. Antar layanan tidak mengirim local filesystem path.

URL fetch melindungi SSRF termasuk DNS/redirect ke alamat privat, metadata cloud, loopback, dan skema selain HTTP(S). Endpoint internal antar modul memakai allowlist konfigurasi server terpisah dari URL input pengguna. Escape HTML/OCR/web content; perlakukan dokumen/retrieval sebagai data yang tidak boleh mengubah instruksi model.

> Catatan: file ini adalah salinan acuan untuk sinkronisasi. Sumber otoritatif tetap kontrak yang tertanam pada masing-masing prompt development. Bila berbeda, ikuti versi `1.0.0` dan koordinasikan tim sebelum mengubah.
