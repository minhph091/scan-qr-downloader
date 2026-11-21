# ScanQRDownloader (Local + LAN Web App)

Ứng dụng Web cục bộ giúp nạp hàng chục ảnh chụp trang sách có chứa mã QR (trực tiếp từ máy tính **hoặc chụp từ điện thoại gửi qua mạng LAN**), tự động giải mã link QR và tải hàng loạt file về thư mục đích bạn chỉ định.

## Tính năng chính
- **Chụp ảnh từ điện thoại gửi qua mạng LAN (`/capture`)**:
  - Quét mã QR hiển thị ngay trên màn hình máy tính để mở trang chụp ảnh trên điện thoại (cùng Wi-Fi / mạng LAN).
  - Chụp ảnh trực tiếp bằng camera sau của điện thoại (hoạt động mượt ngay trên HTTP LAN với cả iOS & Android) hoặc chọn nhiều ảnh từ thư viện gửi lên server.
  - Server tự động lưu ảnh vào **Thư mục lưu ảnh chụp** đã cấu hình sẵn (`captured_images/` mặc định, có thể chọn thư mục bất kỳ trên Windows).
  - Phản hồi ngay trên điện thoại xem ảnh vừa chụp đã đọc được QR hay bị mờ, đồng thời tùy chọn **tự động nạp thẳng vào bảng quét QR trên máy tính** theo thời gian thực.
- **Quét QR đa tầng cho ảnh chụp sách thực tế**: Kết hợp `zxing-cpp` + `OpenCV` (tiền xử lý tương phản CLAHE, adaptive threshold, cắt ô lưới chồng lấp cho QR nhỏ nằm góc trang sách, nhận diện vùng ROI vuông, tự xoay theo EXIF, hỗ trợ cả ảnh `.HEIC` từ iPhone).
- **Tự động tách nhiều file MP3 từ 1 mã QR duy nhất**:
  - Hỗ trợ QR dẫn tới **trang web danh sách nghe (playlist HTML)** — tự trích xuất toàn bộ file từ `<audio>`, `<source>`, `<a>`, `data-src`/`data-mp3`, mảng JSON/JS trong `<script>` (kể cả URL tương đối & escaped JSON), link Google Drive/Dropbox nhúng, và playlist `.m3u`.
  - Hỗ trợ QR dẫn tới **thư mục Google Drive công khai** (`drive.google.com/drive/folders/...`) — tự lấy toàn bộ danh sách file MP3/PDF bên trong thư mục theo đúng thứ tự tự nhiên.
  - Hỗ trợ 1 mã QR chứa nhiều URL trên nhiều dòng.
  - Tùy chọn lọc **Chỉ lấy file âm thanh (MP3, M4A, WAV...)**, mặc định **lưu trực tiếp tên gốc của file (không dấu tiếng Việt / ký tự `~`) và gom chung vào 1 thư mục duy nhất** không chia thư mục con.
- **Hỗ trợ nhiều loại link tải thực tế**: Link trực tiếp (PDF, MP3, MP4, ZIP...), link rút gọn (follow redirect), **Google Drive** (kể cả file lớn có trang xác nhận virus scan), **Google Docs/Sheets/Slides**, **Dropbox**, **OneDrive/SharePoint**.
- **Tải song song & an toàn**: Tải bất đồng bộ nhiều luồng (tùy chỉnh 1–10 luồng), ghi tạm `.part` trước khi đổi tên (atomic write), tự động retry khi rớt mạng, theo dõi tiến độ theo thời gian thực qua SSE.
- **Quản lý tên file & thư mục thông minh**: Hộp thoại chọn thư mục chuẩn Windows, mở nhanh thư mục đích trong Explorer, đặt tên kèm tiền tố thứ tự chương (`01_...`) hoặc tên ảnh gốc, khử trùng lặp URL, sửa tay URL nếu ảnh bị quá mờ, xuất báo cáo CSV.

## Cách chạy ứng dụng

### Cách 1: Nhấn đúp file `start.bat`
Mở thư mục dự án và chạy [start.bat](start.bat).
- Máy tính tự động mở giao diện quản lý tại: `http://127.0.0.1:8765`
- Điện thoại (cùng mạng Wi-Fi / LAN) mở link LAN hiển thị trên terminal hoặc quét mã QR trong mục **3. Chụp ảnh từ Điện thoại gửi qua mạng LAN** (`http://<IP-LAN-cua-PC>:8765/capture`).

### Cách 2: Chạy qua terminal (PowerShell)
```powershell
.\.venv\Scripts\python.exe run.py
```

### Lưu ý Windows Firewall (khi truy cập từ điện thoại trong LAN)
Lần đầu chạy, nếu Windows Defender Firewall hiện hộp thoại hỏi quyền truy cập mạng, hãy tích chọn **Private networks** rồi bấm **Allow access**. Nếu điện thoại vẫn chưa vào được cổng `8765`, chạy PowerShell với quyền Administrator:
```powershell
New-NetFirewallRule -DisplayName "ScanQRDownloader LAN 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

## Chạy kiểm thử tự động
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```
