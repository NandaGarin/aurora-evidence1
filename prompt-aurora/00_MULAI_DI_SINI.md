# Paket prompt implementasi AURORA untuk tiga pengembang

Paket ini berisi tiga prompt lengkap untuk dijalankan di agen coding (mis. Codex/OpenCode), satu prompt untuk setiap orang. Setiap prompt mencakup aplikasi mandiri, metode utama, backend/API, frontend, penyimpanan, pengujian, evaluasi, deployment lokal, dan dokumentasi serah terima. **Kontrak data lengkap sudah tertanam pada setiap prompt**, sehingga masing-masing orang dapat memulai hanya dengan satu berkas prompt miliknya.

Versi paket dan kontrak: **1.0.0**.

## Pembagian berkas

| Penerima | Berkas yang dijalankan | Proyek default | Tanggung jawab utama |
| --- | --- | --- | --- |
| Orang 1 | `01_PROMPT_DEVELOPMENT_VISUAL.md` | aurora-visual | Atomisasi klaim, OCR, region/grounding, UOT, konsistensi visual S/C/U. |
| Orang 2 | `02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md` | aurora-evidence | Retrieval multisumber, reranking, deduplikasi, provenance, AI detector gambar/teks yang providernya dapat diganti. |
| Orang 3 | `03_PROMPT_DEVELOPMENT_FUSION_DECISION_SUPPORT.md` | aurora-decision | Verifikasi bukti, fusi, conformal abstention, laporan, human review, integrasi tiga layanan. |

`KONTRAK_BERSAMA.md` menjadi acuan sinkronisasi tim. Salinannya di ketiga prompt harus identik. Jika kontrak diubah setelah implementasi dimulai, koordinasikan versi dan adapter migrasi.

> Repo ini adalah **Development 2 (aurora-evidence)**. Hanya `02_PROMPT_...md` yang relevan untuk dikerjakan di sini. File `01_...` dan `03_...` milik anggota lain dan **tidak** disalin ke repo ini; kompatibilitas dijamin oleh **kontrak bersama v1.0.0**, bukan dengan menyalin kode modul lain.

## Cara mulai (repo ini)

Instruksi singkat untuk agen bila berkas prompt sudah ada di workspace:

```text
Baca seluruh isi prompt-aurora/02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md dan jalankan
penugasan implementasinya sampai end-to-end. Gunakan prompt-aurora/00_MULAI_DI_SINI.md dan
prompt-aurora/KONTRAK_BERSAMA.md sebagai acuan pendamping. Periksa dahulu kondisi repository,
sistem operasi, dan perangkat pengembangan yang tersedia. Pertahankan perubahan yang sudah ada.
Mulai dari alur demo tanpa API key atau GPU, lalu lanjutkan jalur komputasi lokal nyata.
Sediakan .env.example tanpa secret, .gitignore, README.md, dan IMPLEMENTATION_STATUS.md.
Jalankan pemeriksaan yang tersedia dan catat hasil aktual. Jangan berhenti pada rencana.
```

## Integrasi yang disepakati

Alur utama: **caption + gambar → orang 1 → orang 2 → orang 3 → laporan dan human review**.

| Modul | Endpoint utama | API lokal | Frontend lokal | Keluaran yang dimiliki |
| --- | --- | --- | --- | --- |
| 1 | POST /api/v1/analyze | 8101 | 5171 | analysis |
| 2 | POST /api/v1/retrieve | 8102 | 5172 | retrieval |
| 3 | POST /api/v1/fuse; POST /api/v1/pipeline | 8103 | 5173 | decision + orchestrator |

Setiap aplikasi menerima/menghasilkan `AuroraBundle` v1.0.0. Job berjalan asinkron. URL layanan diatur lewat environment. Pertukaran manual memakai JSON atau ZIP portabel berisi bundle dan media.

## Status serah terima

| Status | Bukti yang harus tersedia |
| --- | --- |
| implemented | Kode, adapter, API, UI, persistence, dokumentasi memang ada. |
| demo_verified | Alur fixture/demo dan pemeriksaan yang ditetapkan benar-benar dijalankan. |
| live_verified | Komputasi nyata atau request provider terkait berhasil dijalankan; per capability. |
| research_evaluated | Eksperimen pada data/split yang sah menghasilkan metrik aktual + artefak reproduksi. |

Satu status tidak otomatis membuktikan status lain. Setup/test/demo lulus tidak membuktikan target akurasi penelitian.
