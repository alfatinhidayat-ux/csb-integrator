Berdasarkan instruksi rumus yang diberikan oleh *owner* Anda, berikut adalah rumusnya secara terstruktur yang bisa langsung Anda masukkan ke dalam Excel atau sistem pembukuan Anda:

### 1. Rumus Mencari DPP (Dasar Pengenaan Pajak)

$$\text{DPP} = \frac{\text{Harga Jual} \times 11}{12}$$

**Di Excel, Anda bisa mengetikkan rumus:**
`=(Harga_Jual * 11) / 12`

---

### 2. Rumus Mencari PPN (Pajak Pertambahan Nilai)

$$\text{PPN} = \text{DPP} \times 12\%$$

**Di Excel, Anda bisa mengetikkan rumus:**
`=DPP * 12%` atau `=DPP * 0,12`

---

### Contoh Penerapan Langsung dengan Angka Anda:

Jika **Harga Jual** di data Anda adalah **Rp 2.633.928,57**, maka jalankan rumusnya langkah demi langkah seperti ini:

1. **Hitung DPP-nya dulu:**
* Ditotal dulu: $2.633.928,57 \times 11 = 28.973.214,27$
* Lalu dibagi 12: $28.973.214,27 \div 12 = \mathbf{2.414.434,52}$
* *Maka, angka **Rp 2.414.434,52** inilah yang diinput ke kolom **Harga Satuan / DPP** di e-Faktur.*


2. **Hitung PPN-nya kemudian:**
* $2.414.434,52 \times 12\% = \mathbf{289.732,14}$
* *Maka, angka **Rp 289.732,14** inilah yang akan muncul di kolom **PPN**.*



Dengan rumus dari *owner* ini, nilai DPP di faktur Anda akan otomatis berubah turun, dan PPN-nya pun ikut menyesuaikan.