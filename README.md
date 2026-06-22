# VietDub

VietDub là ứng dụng desktop dịch, lồng tiếng và render video bằng AI. Bản phân
phối chạy giao diện Tauri cùng FastAPI backend cục bộ; dữ liệu video và thông tin
cấu hình nằm trên máy người dùng.

## Nền tảng hỗ trợ

- Windows 10/11 x64. Windows ARM64 có thể chạy bản x64 qua lớp giả lập của Windows.
- macOS Apple Silicon: M1, M2, M3, M4 hoặc mới hơn.
- Intel Mac và Windows ARM64 native chưa nằm trong phạm vi bản đầu.

Các file phát hành:

```text
VietDub-{version}-windows-x64-setup.exe
VietDub-{version}-windows-x64-setup.exe.sha256
VietDub-{version}-macos-arm64.dmg
VietDub-{version}-macos-arm64.dmg.sha256
```

## Cài đặt Windows

1. Tải file `VietDub-*-windows-x64-setup.exe` và file `.sha256` đi kèm.
2. Kiểm tra checksum trong PowerShell:

   ```powershell
   Get-FileHash .\VietDub-*-windows-x64-setup.exe -Algorithm SHA256
   ```

3. Chạy installer. VietDub cài cho tài khoản hiện tại và không cần quyền admin.
4. Nếu SmartScreen xuất hiện ở bản unsigned, chọn **More info** rồi **Run anyway**
   chỉ khi checksum khớp với artifact tin cậy.
5. Mở VietDub từ Start Menu.

## Cài đặt macOS Apple Silicon

1. Tải `.dmg` và file `.sha256`.
2. Kiểm tra checksum:

   ```bash
   shasum -a 256 VietDub-*-macos-arm64.dmg
   ```

3. Mở DMG và kéo VietDub vào Applications.
4. Bản hiện tại chưa notarize. Nhấp phải vào VietDub, chọn **Open**, sau đó xác
   nhận **Open**. Nếu macOS vẫn chặn, vào **System Settings > Privacy & Security**
   và chọn **Open Anyway**.

Nếu một bản thử nghiệm cũ báo **“VietDub is damaged”**, chỉ sau khi checksum khớp
với release tin cậy, mở Terminal và xóa cờ quarantine:

```bash
xattr -dr com.apple.quarantine /Applications/VietDub.app
```

Sau đó nhấp phải VietDub và chọn **Open**. Bản build mới được ký ad-hoc và CI xác
minh toàn bộ app bundle để không còn lỗi chữ ký không nhất quán này.

Không phát hành công khai bản macOS unsigned. Sau khi có Apple Developer ID,
thêm ký ứng dụng và notarization vào workflow trước khi phân phối rộng rãi.

## Thiết lập lần đầu

Lần đầu chạy, VietDub tự mở trang **Cấu hình**. Không cần tạo `.env`.

### Dịch bằng 9router

1. Trong **Cấu hình > Dịch**, chọn cài 9router.
2. VietDub tải Node.js portable và 9router vào thư mục dữ liệu của riêng app.
   Không cần cài Node toàn hệ thống và không cần quyền admin.
3. Chờ trạng thái 9router sẵn sàng rồi mở dashboard `http://127.0.0.1:20128`.
4. Đăng nhập provider/model theo hướng dẫn của 9router.
5. Giữ endpoint `http://127.0.0.1:20128/v1` và model `translate`, hoặc nhập model
   mà profile 9router của bạn cung cấp.

Node.js được ghim ở `v24.17.0`; 9router được ghim ở `0.5.4`. Mọi gói tải xuống
đều được kiểm tra checksum trước khi cài.

### STT bằng Groq hoặc NVIDIA

- Groq: tạo API key tại Groq Console và dán vào ô **Groq API key**.
- NVIDIA Riva/NIM: dán key vào **NVIDIA API key**.
- Nếu video đã có subtitle hoặc dùng hard-sub OCR, có thể không cần STT API.

API key chỉ được lưu trong thư mục dữ liệu người dùng. Không gửi
`runtime-settings.json` cho người khác và không đưa file này lên Git.

### Giọng đọc

- **Edge TTS**: dùng ngay, không cần GPU hay model cục bộ.
- **VieNeu**: model được tải vào cache người dùng ở lần sử dụng đầu.
- **OmniVoice Colab/remote**: nhập URL và key của dịch vụ trong cấu hình giọng.
- **OmniVoice local**: chọn cài runtime. VietDub tạo venv riêng trong app-data rồi
  tải PyTorch/model phù hợp. Máy Mac dùng MPS; Windows ưu tiên NVIDIA CUDA và có
  thể chạy CPU nhưng rất chậm. Trên macOS, cài Python 3.12 từ python.org trước;
  VietDub tự dò cả bản python.org và Homebrew ngay cả khi mở app từ Finder.

VietDub không kèm audio tham chiếu, transcript hay instruction cá nhân. Người
dùng phải tự cung cấp dữ liệu giọng mà mình có quyền sử dụng.

## Dữ liệu người dùng

VietDub không ghi database hoặc video vào thư mục cài đặt.

Windows:

```text
%LOCALAPPDATA%\com.vietdub.app\
```

macOS:

```text
~/Library/Application Support/com.vietdub.app/
```

Cấu trúc chính:

```text
aether_studio.db              database job
storage/                      video, subtitle, audio, output và log
storage/runtime-settings.json API key và cấu hình runtime
runtime/                      OmniVoice helper/runtime
venvs/omnivoice/              môi trường OmniVoice tùy chọn
tools/node/                   Node.js portable
tools/9router/                9router managed
```

Gỡ ứng dụng không tự xóa dữ liệu sản xuất. Để backup, đóng VietDub rồi sao chép
toàn bộ thư mục trên. Để xóa sạch, gỡ ứng dụng và tự xóa thư mục dữ liệu.

## Build từ source

### Yêu cầu chung

- Git
- Node.js `24.17.0`
- Python `3.12`
- Rust stable
- Kết nối internet khi cài dependency

Không dùng `.env` cá nhân để build release. Build script chỉ đóng gói source và
runtime công khai; API key được nhập sau khi cài.

### Windows x64

Cài Visual Studio Build Tools với workload **Desktop development with C++**, rồi:

```powershell
npm run release:windows
```

Script sẽ:

1. Tạo `.build/venv-windows`.
2. Cài backend dependency và PyInstaller.
3. Chạy `npm ci`.
4. Chuẩn bị FFmpeg/ffprobe và kiểm tra encoder/filter bắt buộc.
5. Build FastAPI sidecar.
6. Build NSIS installer.
7. Đặt installer và SHA-256 trong `release/`.

### macOS Apple Silicon

Cài Xcode Command Line Tools:

```bash
xcode-select --install
```

Sau đó:

```bash
bash scripts/build-release.sh
```

Script từ chối chạy trên Intel Mac để tránh tạo nhầm artifact. DMG và checksum
được ghi vào `release/`.

### GitHub Actions

Workflow `.github/workflows/release.yml` chạy thủ công hoặc khi push tag `v*`.
Nó tạo hai artifact Windows x64 và macOS arm64.

Job Mac dùng `macos-14-xlarge`, là runner Apple Silicon và có thể yêu cầu gói
GitHub trả phí. Nếu tổ chức dùng self-hosted Apple Silicon, đổi `runs-on` sang
label của runner đó nhưng giữ nguyên `build-release.sh`.

Khi có chứng chỉ, lưu certificate/password/notarization credentials trong
GitHub Secrets. Không commit `.p12`, provisioning profile hoặc mật khẩu ký app.

## Xuất source sạch từ VideoDubbing

Tại repo nguồn:

```powershell
.\scripts\export-vietdub.ps1 -Destination D:\AgenticAI\VietDub
```

Nếu thư mục đích không trống, script dừng. Chỉ dùng `-Force` sau khi kiểm tra kỹ
đường dẫn. Export hoạt động theo whitelist và chạy secret scan trước khi hoàn tất.

Không được xuất:

- `.env`, database, storage, log, cache hoặc virtualenv.
- API key, OAuth token, cookies và đường dẫn người dùng.
- Audio/video cá nhân hoặc voice reference.
- `node_modules`, build artifact và Git history của repo nguồn.

## Khắc phục lỗi

### Backend không sẵn sàng

Đóng app, mở lại và kiểm tra không có tiến trình VietDub cũ chiếm cổng production `18386` (cổng dev vẫn là `8386`). Log nằm
trong `storage/logs`. Antivirus có thể giữ PyInstaller sidecar ở lần chạy đầu.

### FFmpeg hoặc ffprobe không tìm thấy

Bản installer chuẩn đã bundle cả hai. Nếu trang debug không thấy đường dẫn nằm
trong resource của VietDub, artifact đã được build sai; chạy lại bước
`prepare:media` và build installer, không yêu cầu người dùng tự sửa PATH.

### 9router cài thất bại

Kiểm tra kết nối tới `nodejs.org` và `registry.npmjs.org`, dung lượng app-data,
sau đó thử lại. Xóa riêng `tools/node` và `tools/9router` nếu muốn cài sạch; không
xóa `storage` nếu muốn giữ job.

### Model tải chậm hoặc hết dung lượng

VieNeu, OmniVoice và PyTorch không nằm trong installer. Đảm bảo ổ đĩa còn trống
và không đóng app trong lần tải đầu. Edge TTS là lựa chọn nhẹ để kiểm tra nhanh.

### Render còn giọng gốc

Chế độ `hybrid` chỉ tách vocal khi Demucs khả dụng; nếu không, app trộn âm thanh
nguồn ở mức thấp. Demucs là dependency tùy chọn lớn và không được bundle trong
installer gọn.

## Bảo mật và quyền riêng tư

- Backend chỉ bind `127.0.0.1`.
- Không chia sẻ database, runtime settings, log hoặc source video nếu chứa dữ
  liệu nhạy cảm.
- Chỉ dùng giọng, video và tài khoản AI mà bạn có quyền sử dụng.
- Kiểm tra `THIRD_PARTY_NOTICES.md` và hoàn thiện license bundle trước khi phát
  hành thương mại hoặc công khai.
