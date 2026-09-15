# IMPLEMENTATION_STATUS — AURORA Evidence (Development 2)

Kontrak: **v1.0.0**. Taksonomi status: `implemented`, `demo_verified`,
`live_verified`, `research_evaluated`. **Satu status tidak membuktikan yang lain.**

> ## Batasan lingkungan pembuatan — baca ini dulu
>
> Sesi ini berjalan di sandbox dengan jaringan `INTEGRATIONS_ONLY`:
> **PyPI dan npm registry diblokir** (`403 Forbidden`), dan tidak ada akses
> internet umum. Konsekuensi yang jujur:
>
> 1. `pip install` / `uv sync` / `npm install` **tidak pernah dijalankan** di
>    sini. Karena itu inti sistem sengaja ditulis dengan **Python standard
>    library saja**, supaya benar-benar bisa dijalankan dan diuji tanpa
>    instalasi. Semua yang ditandai `demo_verified` di bawah dijalankan aktual
>    pada **Python 3.11.15**.
> 2. **Skema API vendor detector TIDAK dapat diverifikasi** terhadap dokumentasi
>    resmi. Prompt melarang mengarang URL/skema API, jadi adapter dibuat
>    **deklaratif**: endpoint, auth, dan pemetaan field respons dibaca dari
>    `configs/providers.live.toml` dan seluruh profil vendor ditandai
>    `verified = false`. Lihat "Yang belum diverifikasi".
> 3. FastAPI/Pydantic/React belum bisa dijalankan di sini, sehingga lapisan API
>    HTTP dan frontend **belum ada** (bukan "ada tapi belum diuji").

## Cara memverifikasi ulang (tanpa instalasi apa pun)

```bash
cd backend
PYTHONPATH=. python3.11 -m aurora_evidence.cli selftest
PYTHONPATH=. python3.11 -m aurora_evidence.cli retrieve ../prompt-aurora/contoh/01_INPUT_RETRIEVAL.json
PYTHONPATH=. python3.11 -m aurora_evidence.cli search "banjir Monas Jakarta"
PYTHONPATH=. python3.11 -m aurora_evidence.cli capabilities --mode live --profile ../configs/providers.live.toml
```

## Status per komponen

| Komponen | Status | Bukti |
| --- | --- | --- |
| Kontrak: JCS RFC 8785 (pure stdlib) | `demo_verified` | 16 vektor angka ECMAScript, urutan kunci UTF-16 (emoji astral), penolakan NaN/Inf/duplicate-key/BOM/lone-surrogate/int di luar rentang aman, hash order-independent. |
| Kontrak: vocabulary tertutup (`labels.py`) | `implemented` | Seluruh enum kontrak. |
| Kontrak: validator 16 invarian (`validate.py`) | `demo_verified` | 14 kasus pelanggaran bundle + 14 kasus forensic signal + 8 kasus invarian 1 semuanya tertangkap dengan kode yang benar. |
| Kontrak: identitas/ID + `atom_set_id` | `demo_verified` | Deterministik, order-independent; mismatch/dangling/cyclic/span-out-of-range terdeteksi. |
| Query planner deterministik | `demo_verified` | 3 variasi query dijalankan pada alur demo. |
| Local corpus BM25 (**jalur lokal nyata**) | `demo_verified` | 9 dokumen, BM25 Okapi pure-python, me-ranking dokumen relevan di atas. |
| Dedup + independence grouping | `demo_verified` | Ambang **diukur**, bukan ditebak (lihat catatan di bawah). Sindikasi 2 domain → 1 grup; artikel event sama yang ditulis independen tetap terpisah. |
| Temporal eligibility + credibility indicator | `demo_verified` | Tri-state: tanggal tak diketahui → `null`; bukti setelah `as_of` → `false` tapi tetap tampil. |
| Ranking RRF → reranker fitur → MMR/group cap | `demo_verified` | `score_breakdown` memuat fitur mentah + kontribusi berbobot; salinan sindikasi digeser ke ekor (bukan dibuang). |
| Forensics: interfaces + normalisasi terpusat | `demo_verified` | Invarian 11/15 ditegakkan di satu tempat; 14 kasus lulus. |
| Forensics: registry + 2 profil konfigurasi | `demo_verified` | Fixture **ditolak** saat `mode=live`; nama profil salah → `unconfigured`, bukan fallback fixture. |
| Forensics: fixture detector (image+text) | `demo_verified` | Deterministik, berlabel; teks pendek & bahasa tak didukung → `unsupported` + skor `null`. |
| Forensics: adapter HTTP deklaratif | `implemented` | Auth (bearer/header/basic/form), multipart & base64 upload, polling async, error mapping 401/403/402/429+Retry-After/413/415/422/5xx/timeout/unreachable/malformed/field-missing, redaksi secret. **Belum pernah dipanggil ke layanan nyata.** |
| Pipeline end-to-end → `Retrieval` | `demo_verified` | Output divalidasi kontrak sebelum dikembalikan; 14/14 selftest lulus. |
| CLI (`selftest`/`retrieve`/`search`/`capabilities`/`detect-text`/`validate-bundle`/`corpus-stats`) | `demo_verified` | Output aktual ada di bagian berikut. |
| **Backend FastAPI + endpoint kontrak** | **belum ada** | `/health`, `/ready`, `/api/v1/media`, `/api/v1/retrieve`, `/api/v1/jobs/{id}` belum diimplementasikan. |
| **Worker job persisten + idempotency** | **belum ada** | Lease/timeout/recovery, `Idempotency-Key` belum ada. |
| **Persistence (SQLite) + riwayat kasus** | **belum ada** | Lihat ADR yang diusulkan di NEXT_STEPS.md. |
| **Frontend React/Vite (4 tab)** | **belum ada** | Tab Bukti / Asal Gambar / Deteksi AI / Jejak Pencarian belum dibuat. |
| **Ekspor/impor ZIP aman** | **belum ada** | Penolakan path traversal/zip bomb/symlink belum diimplementasikan. |
| **Provenance gambar (dHash/local image index)** | **belum ada** | `image_matches` saat ini selalu `[]`. |
| **Adapter web search / MAFINDO / reverse image** | **belum ada** | Interface `SearchProvider` sudah siap; adapter konkret belum dibuat. |
| **Content fetcher + SSRF guard** | **belum ada** | Diperlukan sebelum ada sumber web. |
| **Evaluasi retrieval (Recall/nDCG/MRR + qrels)** | **belum ada** | Metrik dedup & diversity sudah dihitung; qrels dan metrik IR belum. |
| Tests sebagai suite pytest | **belum ada** | Verifikasi saat ini berbentuk skrip asersi + `cli selftest`, bukan `tests/` pytest. |
| Docs (`docs/*.md`) | **belum ada** | Hanya README + file ini + NEXT_STEPS.md. |
| Docker / docker-compose | **belum ada** | — |

## Pemeriksaan yang benar-benar dijalankan

`python3.11 -m aurora_evidence.cli selftest` → **14/14 lulus**:

```
PASS  contoh input demo valid terhadap kontrak
PASS  output bundle valid terhadap kontrak
PASS  ada bukti dari corpus lokal nyata  (9 bukti)
PASS  atom_set_id null tanpa analysis (inv 2)
PASS  semua atom_ids kosong tanpa analysis (inv 2)
PASS  excerpt selalu substring content.text (inv 7)
PASS  content.sha256 == sha256(UTF-8 text) (inv 7)
PASS  dedup menghasilkan >1 kelompok independen  (8 grup)
PASS  sindikasi lintas domain -> satu independence group (inv 8)  (2 salinan)
PASS  tanggal tak diketahui -> temporal_eligible null  (1 bukti)
PASS  bukti setelah as_of -> false, tetap tampil  (2 bukti)
PASS  detector terpisah dari evidence_list (inv 6)  (1 signal)
PASS  teks pendek/bahasa tak didukung -> unsupported + skor null (inv 15)
PASS  tidak ada signal 'likely_ai_generated' saat status != ok (inv 11)

dedup    : items=9 duplicate_clusters=8 independence_groups=8
diversity: items=9 independent_groups=8 providers=1 group_ratio=0.888889
```

### Kalibrasi ambang near-duplicate (diukur, bukan ditebak)

Ambang awal Hamming ≤ 3 **gagal** mendeteksi salinan sindikasi yang ditambah satu
kalimat. Pengukuran aktual pada corpus demo:

| Pasangan | SimHash Hamming | Shingle Jaccard |
| --- | --- | --- |
| wire story + 1 kalimat redaksi | 6 | 0.862 |
| wire story + prefiks judul | 4 | 0.926 |
| event sama, ditulis independen | 30 | 0.000 |
| topik berbeda | 32 | 0.000 |

Karena itu SimHash dipakai sebagai *candidate gate* (≤ 12) lalu **dikonfirmasi**
shingle Jaccard (≥ 0.7). Margin 0.86 vs 0.00 lebar, sehingga ambang ini tidak
fine-tuned pada data uji.

## Yang belum diverifikasi / belum tersedia

| Kebutuhan | Status spesifik |
| --- | --- |
| Skema API Sightengine (image) | **Belum diverifikasi.** `configs/providers.live.toml` memuat kerangka `verified = false` dengan penanda `TODO(verifikasi)` pada endpoint, `score_path`, dan **arah skala**. Wajib dicek ke dokumentasi resmi lalu dicatat di `docs/providers.md`. |
| Skema API GPTZero (text) | **Belum diverifikasi**, sama seperti di atas. Perhatikan arah skor: sebagian vendor mengembalikan probabilitas *human*, bukan *AI* — salah arah membuat kesimpulan terbalik. |
| Dukungan bahasa Indonesia pada kedua detector | **Belum diverifikasi.** Profil live saat ini membatasi `languages = ["en"]`, sehingga caption Indonesia akan `unsupported` — bukan diklaim "ditulis manusia". |
| API key detector | Belum ada. Tanpa kunci → `unconfigured`/`unavailable`, tanpa fallback fixture. |
| Web search (Tavily/Serper), MAFINDO, reverse image search | Belum ada kunci **dan** belum ada adapter. Local corpus tetap berfungsi tanpa kredensial. |
| Dense retrieval multilingual | Belum; butuh unduhan model. BM25-only tetap berguna. |
| Dataset evaluasi (AVerImaTeC / MOCHEG) | Belum diakses; lisensi/label/bahasa belum diverifikasi. |
| `live_verified` untuk provider apa pun | **Tidak ada.** Belum ada satu pun request nyata yang berhasil. Adapter lengkap + test bukan bukti live. |
| `research_evaluated` | **Tidak ada.** Corpus demo bersifat sintetis dan bukan bukti performa ilmiah. |

## Catatan penting untuk Dev 3

`retrieval` yang dihasilkan sudah lolos validator kontrak yang sama yang akan
Anda pakai untuk mengimpor. Yang perlu diperhatikan:

- Tanpa `analysis`, `atom_set_id = null` dan semua `atom_ids = []`. Jangan
  menempelkan `atom_set_id` baru tanpa remapping eksplisit.
- `independence_group_id` sudah siap dipakai untuk mencegah voting ganda;
  `provenance.grouping_basis` (di `score_breakdown`/ekstensi) menjelaskan aturan
  yang memicu pengelompokan.
- `temporal_eligible = null` berarti **tanggal tidak diketahui**, bukan layak.
- `forensic_signals` **bukan** bukti dan **bukan** stance. Sinyal dengan
  `status != "ok"` tidak boleh dibaca sebagai indikasi apa pun tentang isi.
- `relevance_score` adalah `logistic(rerank_score)` untuk tampilan/threshold —
  **bukan** probabilitas kebenaran klaim.
