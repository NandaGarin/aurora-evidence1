# Contoh masukan AURORA (paket prompt)

Contoh berikut adalah **masukan sintetis berlabel demo**, bukan keluaran aplikasi nyata dan bukan data evaluasi ilmiah. Angka, hash, dan referensi disusun untuk menguji **kontrak**, bukan menyatakan akurasi model. Keduanya **tidak menyertakan gambar**.

## `01_INPUT_RETRIEVAL.json` — untuk Development 2 (aurora-evidence)

Klaim demo tanpa `analysis`. Dipakai menguji jalur retrieval mandiri Orang 2 tanpa perlu output Development 1.

Perilaku yang perlu diperiksa:

- Retrieval berjalan dengan `analysis=null` dan `atom_ids=[]`; caption utuh dipakai sebagai query.
- `Retrieval.atom_set_id` tetap `null` karena tidak ada analysis.
- Provider tanpa kredensial tampil sebagai `unconfigured`/`unavailable` (bukan gagal senyap, bukan fixture diam-diam).
- Local corpus (BM25) menghasilkan bukti nyata; `forensic_signals` (AI detector) terpisah dari `evidence_list`.
- Semua `run.mode` harus `demo` sesuai `bundle.mode`.

## `02_INPUT_FUSION_DEMO.json` — untuk Development 3 (aurora-decision)

Berkas ini milik Development 3 (anotasi dua atom + bukti sintetis). **Tidak diperlukan** untuk mengerjakan Development 2, sehingga tidak disertakan di paket kerja repo ini. Lihat paket asli bila dibutuhkan untuk pengujian integrasi lintas modul.
