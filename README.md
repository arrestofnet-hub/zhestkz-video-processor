# zhestkz-video-processor

Бесплатный on-demand видеопроцессор для проекта **ЖЕСТЬ KZ** на GitHub Actions.

Что делает:
- принимает видео, фото или только текст;
- делает вертикальный ролик 1080×1920 без растягивания;
- режет длинные ролики до 45 секунд;
- нормализует звук;
- распознаёт русскую речь через faster-whisper;
- прожигает субтитры;
- добавляет заголовок и бренд «ЖЕСТЬ KZ»;
- отдаёт готовый H.264 MP4 как GitHub Actions artifact;
- умеет отправлять статус обратно в Cloudflare callback.

## Первый тест

1. Открой вкладку **Actions**.
2. Выбери workflow **Zhest KZ Video Processor**.
3. Нажми **Run workflow**.
4. Для простого теста оставь `media_url` пустым и title `Тест ЖЕСТЬ KZ`.
5. После завершения скачай artifact `zhestkz-video-manual-test`.

Следующий этап: Cloudflare Worker автоматически запускает `repository_dispatch`, когда выбирает горячую новость.
