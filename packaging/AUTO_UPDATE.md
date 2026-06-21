# Cập nhật tự động (Auto-update) — VietDub

Tài liệu giải thích cơ chế cập nhật của VietDub: "ký update" là gì, người dùng
cần làm gì, và maintainer phát hành bản mới ra sao.

## 1. Tổng quan

VietDub dùng `tauri-plugin-updater`. Khi mở app, app tự kiểm tra GitHub Releases
xem có bản mới không. Nếu có, app tải bản cập nhật **đã ký**, kiểm tra chữ ký,
cài đè và tự khởi động lại — người dùng không phải tải tay hay cài lại từ đầu.

## 2. "Ký update" (code signing) là gì?

App tự tải file cài đặt từ Internet rồi tự chạy. Để không ai chèn được file giả
(virus), mỗi bản cập nhật phải có **chữ ký số** của chính maintainer.

Cơ chế gồm một cặp khoá:

- **Private key (khoá bí mật)** — maintainer giữ, không chia sẻ. File:
  `.build/updater-signing/vietdub_updater.key` (đã gitignore, không nằm trong
  bản phân phối).
- **Public key (khoá công khai)** — nhúng sẵn trong app
  (`apps/desktop/src-tauri/tauri.conf.json` → `plugins.updater.pubkey`).

Luồng:

1. Khi build bản mới, máy build dùng **private key** ký file cài đặt → tạo file
   chữ ký (`.sig`) + `latest.json`.
2. App ở máy người dùng tải về, dùng **public key** kiểm tra chữ ký.
3. Khớp → đúng bản của maintainer → cài. Không khớp → từ chối, không cài.

→ Chỉ maintainer (người giữ private key) mới tạo được bản update hợp lệ. Tauri
**bắt buộc** có chữ ký này thì updater mới hoạt động.

> ⚠️ Mất private key = không ký được update cho các bản người dùng đã cài. Hãy
> sao lưu an toàn, không commit, không chia sẻ.

## 3. Người dùng cần làm gì?

Gần như không:

1. Mở app như bình thường.
2. App tự báo khi có bản mới: banner *"Đã có bản cập nhật X. [Cập nhật & khởi
   động lại]"*.
3. Bấm nút → app tự tải, kiểm tra chữ ký, cài, mở lại. Xong.

Không cần quyền admin (cài vào thư mục dữ liệu người dùng). Bỏ qua banner thì app
vẫn chạy bản cũ, cập nhật lần sau cũng được.

## 4. Maintainer phát hành bản mới thế nào?

**Một lần duy nhất** — thêm GitHub secret (Settings → Secrets and variables →
Actions):

- `TAURI_SIGNING_PRIVATE_KEY` = toàn bộ nội dung file
  `.build/updater-signing/vietdub_updater.key`
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` — chỉ cần nếu key có đặt mật khẩu (key
  hiện tại không có).

**Mỗi lần ra bản mới:**

1. Tăng version cho khớp ở 3 chỗ: `apps/desktop/src-tauri/tauri.conf.json`
   (quan trọng nhất — updater so version theo file này), `apps/desktop/package.json`,
   và hằng `APP_VERSION` trong `apps/desktop/src/App.tsx`.
2. Tạo + đẩy tag:
   ```
   git tag v0.1.1
   git push origin v0.1.1
   ```
3. Workflow `.github/workflows/release.yml` (tauri-action) tự build, ký, sinh
   `latest.json` và tạo một GitHub Release dạng **draft**. Vào tab Releases sửa
   nội dung rồi bấm **Publish**.

Sau khi Release được publish, app của người dùng sẽ thấy bản mới ở lần mở kế tiếp.

> Nếu chưa set secret `TAURI_SIGNING_PRIVATE_KEY` mà đã đẩy tag: build sẽ **lỗi**
> ở bước ký (vì `createUpdaterArtifacts` cần khoá). Set secret xong thì vào
> Actions bấm **Re-run** job đó, không cần tạo lại tag.

## 5. (Tuỳ chọn) cập nhật riêng phần backend

Ngoài updater toàn app còn có track cập nhật riêng backend
(`apps/desktop/src-tauri/src/managed_backend.rs`) để khỏi tải lại cả app khi chỉ
đổi backend. Dùng `scripts/make-backend-manifest.ps1` sinh `backend-manifest.json`
rồi đăng lên Release. Đây là tính năng phụ, mặc định app vẫn chạy backend bundled.
