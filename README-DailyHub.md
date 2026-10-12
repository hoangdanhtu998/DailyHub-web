# DailyHub gộp tin Việt Nam, AI, Devs, GitHub và Prompt Spider

1. Mở FreeLLMAPI Desktop nếu cần dùng Prompt Spider đồng bộ Analytics.
2. Chạy `Run-FreeLLMAPI-Spider.bat`.
3. Mở `http://127.0.0.1:8766/index.html`.
4. Trang mặc định vào **Tất cả tin**: gộp tin RSS Việt Nam theo nhiều chủ đề, AI, Devs & Tech, và repo GitHub AI mới nổi. Các tab riêng, tin đã lưu, ẩn tin đã xem, tìm kiếm và Prompt Spider vẫn có sẵn.

RSS được tải từ các nguồn công khai; mỗi feed lỗi sẽ được bỏ qua và số feed hoạt động hiển thị cạnh bộ đếm. Dữ liệu được gộp để giảm bài trùng. Danh sách có phân trang và tự kiểm tra cập nhật định kỳ. Thẻ tin hiện trích đoạn RSS thật, không chỉ tiêu đề.

Tab Deal lọc các bài từ RSS MGG.CX và Deal1K khi tiêu đề/trích đoạn nêu rõ mức giảm từ 20% trở lên. Hai nút Facebook mở tìm kiếm bài viết/nhóm trong Facebook. Tự động đọc bài trong trang/nhóm Facebook cần ứng dụng Meta, token và quyền API đã được cấp; DailyHub không giả lập kết quả Facebook.

Thẻ tin tiếng Anh/ngoại ngữ có nút dịch tiêu đề và trích đoạn sang tiếng Việt, giữ nguyên bản gốc để đối chiếu. Deal hiển thị link sản phẩm và giá nếu RSS cung cấp; nếu chỉ có bài tổng hợp thì dẫn tới bài deal và ghi rõ, không tự bịa giá.

Prompt Spider kiểm tra event mới mỗi giây và chạy từng request thật sau khi FreeLLMAPI ghi log kết quả. Spider hiển thị model, trạng thái, số lần thử/lỗi, token và độ trễ; Analytics không lưu prompt của request API thông thường nên không có nội dung prompt để hiển thị. Số liệu tổng hợp làm mới mỗi hai giây.

Bridge đọc cơ sở dữ liệu FreeLLMAPI ở chế độ chỉ đọc và chỉ lắng nghe trên `127.0.0.1`. Dừng máy chủ bằng Ctrl+C trong cửa sổ chạy batch.

## Tính năng mới

- **Từ khóa theo dõi:** thêm từ khóa (ví dụ "Gemini") ở khung phía trên danh sách; tin khớp sẽ có nhãn ◉, và nút "Chỉ tin theo dõi" lọc riêng các tin đó.
- **Chế độ gọn:** nút "Gọn" ẩn ảnh và tóm tắt để xem nhiều tin hơn.
- **Thống kê đọc:** số tin đã mở hôm nay và trong 7 ngày, lưu cục bộ tối đa 90 ngày.
- **Xuất/nhập dữ liệu:** ở chân trang, xuất JSON gồm tin đã xem, tin đã lưu, từ khóa, thống kê; nhập lại sẽ gộp với dữ liệu hiện có.
- **Phím tắt:** nhấn "/" để tìm kiếm, Esc để xóa nội dung tìm kiếm.
- **Cài như ứng dụng (PWA):** trình duyệt hỗ trợ có thể cài DailyHub; vỏ giao diện được lưu để mở lại khi mất mạng. Tin và dữ liệu API luôn lấy mới.
- **Tổng quan:** nút "Tổng quan" trên thanh đầu mở trang có đồng hồ, số tin chưa đọc (lấy từ kênh tin gần nhất), số tin đã đọc hôm nay, việc cần làm, ghi chú nhanh và danh sách ghi chú. Dữ liệu được xuất/nhập cùng bản sao lưu.

---

# AI Personal Growth Hub

Mở rộng Daily Hub thành trung tâm cá nhân về AI/LLM. Tin tức vẫn là trang chính và không thay đổi hành vi cũ.

## Kiến trúc

- `freellmapi_spider_bridge.py`: máy chủ cục bộ (127.0.0.1:8766). Vẫn phục vụ giao diện, Prompt Spider và thêm API `/api/hub/*`. Nó cũng khởi động bộ lập lịch chạy nền.
- `hub/store.py`: SQLite trong `data/dailyhub.db` (đổi bằng biến `DAILYHUB_DATA`). Schema có phiên bản và migration, nên nâng cấp không làm mất dữ liệu cũ.
- `hub/sources.py`: GitHub REST API và RSS/Atom. Có timeout, retry có backoff cho 429/5xx/lỗi mạng, hạn mức request mỗi lần chạy, và chặn URL trỏ tới mạng nội bộ.
- `hub/pipeline.py`: đồng bộ mỗi nguồn độc lập. Lỗi một nguồn không dừng các nguồn khác. Cập nhật tài nguyên không ghi đè trạng thái cá nhân (đã lưu, đã xem, đánh giá, ghi chú).
- `hub/scoring.py`: điểm 0-100 chia 7 thành phần có lý do rõ ràng: hoạt động, release, độ phổ biến (stars, thang log), cộng đồng (forks), tình trạng lưu trữ, mô tả/homepage, giấy phép. Stars không phải tiêu chí duy nhất.
- `hub/scheduler.py`: chạy trong tiến trình server, không dùng timer của trình duyệt.
- `hub/api.py`: API cho tài nguyên, nguồn, prompt, memory, sao lưu, cài đặt và xóa dữ liệu.
- `library.js`: giao diện Thư viện AI và phần dashboard trong Tổng quan. Mọi dữ liệu được hiển thị bằng `textContent`, không dùng `innerHTML`.

## Chạy

1. Chạy `Run-FreeLLMAPI-Spider.bat` (hoặc `python freellmapi_spider_bridge.py`).
2. Mở `http://127.0.0.1:8766/index.html`, chọn **Thư viện AI** hoặc **Tổng quan**.
3. Dữ liệu tài nguyên được điền sau lần đồng bộ đầu tiên. Bấm **Chạy ngay** ở Tổng quan để chạy ngay, hoặc đợi đến giờ đã đặt.

## Thời tiết

- Nguồn: [Open-Meteo](https://open-meteo.com), miễn phí, không cần API key. Dữ liệu CC BY 4.0, được ghi rõ trên thẻ.
- Thẻ "Thời tiết" ở Tổng quan hiển thị nhiệt độ hiện tại, cảm giác, độ ẩm, gió, và dự báo 6 giờ tới (nhiệt độ, khả năng mưa, mô tả).
- Mặc định là Hà Nội. Đổi tên, vĩ độ, kinh độ và múi giờ trong **Thư viện AI → Nguồn & cài đặt**.
- Dữ liệu được lưu tạm 15 phút trong bộ nhớ server. Nếu không kết nối được, hệ thống hiển thị bản lưu gần nhất và ghi chú là dữ liệu cũ.

## Tìm kiếm chung

Tab **Thư viện AI → Tìm kiếm** tìm cùng lúc trong tài nguyên (tên, mô tả, tác giả, topic), memory, prompt và ghi chú tài nguyên. Kết quả khớp tên/tiêu đề được xếp lên trước. Từ khóa cần ít nhất 2 ký tự, không phân biệt hoa thường với chữ cái ASCII. Bấm "Mở" để chuyển sang đúng mục.

## Sao lưu tự động

- Sau mỗi lần đồng bộ hằng ngày, hệ thống tự tạo một bản sao lưu JSON trong `data/backups/`. Bật/tắt và số bản giữ lại (mặc định 14) nằm trong **Nguồn & cài đặt**.
- Tạo ngay bằng nút "Tạo bản sao lưu ngay", khôi phục bằng nút "Khôi phục" (gộp, không xóa dữ liệu hiện có), hoặc xóa từng bản.
- Bản sao lưu không chứa GitHub token.

## Lịch tự động hằng ngày

- Mặc định: 07:00, múi giờ `Asia/Ho_Chi_Minh`. Đổi trong **Thư viện AI → Nguồn & cài đặt**.
- Lịch chỉ chạy khi tiến trình server đang mở. Nếu máy tắt đúng giờ, lần chạy diễn ra khi server khởi động lại trong ngày (chạy bù một lần).
- Để chạy khi máy khởi động, đặt `Run-FreeLLMAPI-Spider.bat` vào shortcut trong thư mục Startup (`shell:startup`) hoặc tạo tác vụ trong Windows Task Scheduler với trigger "At log on".
- Múi giờ cần dữ liệu `tzdata` trên Windows: chạy `pip install tzdata`. Nếu thiếu, hệ thống dùng UTC+7 cố định cho Asia/Ho_Chi_Minh và hiển thị cảnh báo.
- Trạng thái lịch, lần chạy gần nhất và lỗi từng nguồn hiển thị ở dashboard của Tổng quan.

## Cấu hình API key

- GitHub token là tùy chọn, đặt bằng biến môi trường `GITHUB_TOKEN` trên máy chạy server, rồi khởi động lại server. Token không được lưu vào database, không xuất trong bản sao lưu, không hiển thị trên giao diện và không ghi vào log.
- Không có token, GitHub giới hạn khoảng 60 request/giờ. Hệ thống dùng tối đa 50 request mỗi lần chạy và luân phiên các topic theo lần thành công gần nhất, nên topic nào cũng được cập nhật qua các ngày.
- Không thêm secret vào JavaScript hay repo.

## Nguồn dữ liệu

- GitHub theo topic (mặc định: ai-agents, llm, mcp, agent-skills, rag, local-llm, context-engineering). Đổi danh sách trong cài đặt.
- Theo dõi tác giả/tổ chức GitHub (nút "Theo dõi" trong Nguồn & cài đặt).
- RSS/Atom công khai (thêm trong Nguồn & cài đặt).
- Chỉ dùng API/RSS chính thức. Không thu thập nội dung cần đăng nhập, không vượt CAPTCHA.

Mỗi tài nguyên có: loại (repo/skill/toolkit/bài viết), URL nguồn, tác giả, mô tả đã làm sạch HTML, topic, ngày cập nhật nguồn, thời điểm lấy số liệu, điểm và giải thích điểm. Số liệu chưa kiểm tra được hiển thị là "chưa xác minh" thay vì bị đoán.

Sự kiện được ghi lại: repo mới, release mới, repo bị xóa/không truy cập được (404), repo đã lưu trữ, và repo hoạt động lại.

## Sao lưu và chuyển máy

- **Thư viện AI → Nguồn & cài đặt → Sao lưu**: xuất JSON gồm memory, prompt (kèm lịch sử phiên bản), trạng thái tài nguyên (đã lưu, đã xem, đánh giá, ghi chú), nguồn và cài đặt. Không gồm GitHub token.
- Nhập lại trên máy khác sẽ gộp dữ liệu: bản ghi đã có cùng id sẽ được bỏ qua, không ghi đè. Tệp tối đa 20 MB.
- Có thể nhập memory và prompt từ Markdown: mỗi mục bắt đầu bằng `## Tiêu đề`.
- Xóa dữ liệu: chỉ xóa dữ liệu cá nhân, hoặc xóa toàn bộ. Cần gõ `XOA` để xác nhận.

## Quyền riêng tư và an toàn

- Dữ liệu cá nhân chỉ nằm trên máy chạy server. Không có analytics hay telemetry.
- Mọi ghi dữ liệu qua API cần header `X-DailyHub: 1` và Origin hợp lệ, nên trang web khác không thể ghi vào thư viện của bạn.
- Log server không ghi query string (từ khóa tìm kiếm).
- Body request tối đa 2 MB (sao lưu 20 MB). Số dòng và độ dài trường đều có giới hạn.
- RSS: chỉ http/https, không chấp nhận địa chỉ localhost/mạng nội bộ.

## Giới hạn đã biết

- Lịch chỉ chạy khi server đang mở (xem trên).
- Không đọc nội dung README để chấm điểm; mục docs chỉ dựa trên mô tả và homepage. Không kiểm tra version trong file.
- Không theo dõi trực tiếp GitHub Releases qua feed; release được kiểm tra qua API khi có đủ hạn mức.
- RSS: redirect trỏ tới mạng nội bộ chưa được chặn ở tầng HTTP (chỉ chặn ở địa chỉ ban đầu).
- Skills: hiện phân loại theo topic (`agent-skills`, `claude-skill`). Chưa có trường hướng dẫn cài đặt và tương thích tự động; không điền dữ liệu giả.
- Chưa có tìm kiếm chung xuyên suốt tin, tài nguyên, prompt và memory; mỗi phần có ô tìm kiếm riêng.
- Chưa có chế độ so sánh câu trả lời nhiều AI, AI Hub, Deal Hub và đồng bộ tài khoản giữa thiết bị.
- Thời tiết: đã kiểm thử bằng server giả đúng định dạng Open-Meteo. Chưa kiểm tra gọi thật từ môi trường phát triển (không truy cập được API từ đó), nên cần xác nhận khi chạy trên máy của bạn. Đây là các bước tiếp theo trong lộ trình.
- Chưa có chức năng gộp trùng resource giữa các nguồn khác nhau (URL khác nhau cho cùng một dự án); chỉ gộp trùng theo URL chuẩn hóa.

## Kiểm tra

```
python -m unittest discover -s tests -v     # backend: lưu trữ, điểm, nguồn, scheduler, API, HTTP layer
```

Test backend dùng server GitHub giả cục bộ, không gọi mạng thật. Đã kiểm tra thêm bằng cách chạy giao diện thật qua jsdom với server thật (xem báo cáo bàn giao).
