import os
import re
import json
import asyncio
import tempfile
import shutil
from urllib.parse import urlparse
from .. import loader, utils

try:
    from yt_dlp import YoutubeDL
except Exception:
    YoutubeDL = None

try:
    import aiohttp
except Exception:
    aiohttp = None


DEFAULT_BIGJPG_API_KEY = 'PASTE_KEY_HERE'


@loader.tds
class bedrockTT(loader.Module):
    '''Downloader + Bigjpg upscaler for coddrago/Heroku userbot'''

    strings = {
        'name': 'bedrockTT',
        'no_ytdlp': '<b>yt-dlp не установлен.</b>\n<code>pip install -U yt-dlp[default]</code>',
        'no_ffmpeg': '<b>ffmpeg не найден.</b>',
        'no_aiohttp': '<b>aiohttp не установлен.</b>\n<code>pip install aiohttp</code>',
        'no_key': '<b>нет Bigjpg API key.</b>\n<blockquote>замени в файле:\n<code>PASTE_KEY_HERE</code></blockquote>',
        'no_link': '<b>дай ссылку.</b>\n<code>.tt https://...</code>',
        'bad_link': '<b>это не http/https ссылка.</b>',
        'downloading': '<b>скачиваю максимум качества без перекодирования...</b>',
        'too_big': '<b>файл слишком большой:</b> <code>{} MB</code>\n<blockquote>лимит: <code>{} MB</code></blockquote>',
        'tt_done': '<b>готово.</b>\n<blockquote>без перекодирования. максимум источника.</blockquote>',
        'up_no_input': '<b>дай ссылку или ответь на фото.</b>\n<code>.up 4x</code> или <code>.up photo 2x https://...</code>',
        'uploading': '<b>загружаю фото...</b>',
        'creating': '<b>создаю задачу Bigjpg...</b>',
        'waiting': '<b>задача создана:</b> <code>{}</code>\n<blockquote>жду результат...</blockquote>',
        'not_ready': '<b>ещё не готово.</b>\n<code>{}</code>\n<blockquote>потом: <code>.us {}</code></blockquote>',
        'up_done': '<b>готово.</b>\n<blockquote>Bigjpg · {} · noise {}</blockquote>',
        'error': '<blockquote><b>ошибка</b>\n<pre>{}</pre></blockquote>',
        'check': '<b>bedrockTT</b>\n<b>yt-dlp:</b> <code>{}</code>\n<b>ffmpeg:</b> <code>{}</code>\n<b>aiohttp:</b> <code>{}</code>\n<b>Bigjpg key:</b> <code>{}</code>\n<b>limit:</b> <code>{} MB</code>',
    }

    def __init__(self):
        self.max_size_mb = 1900
        self.cookie_file = None
        self.bigjpg_api = 'https://bigjpg.com/api/task/'
        self.upload_url = 'https://0x0.st'
        self.poll_attempts = 18
        self.poll_delay = 10
        self.max_upload_mb = 95

    def _bigjpg_key(self):
        key = os.environ.get('BIGJPG_API_KEY') or os.environ.get('BIGJPG_KEY') or DEFAULT_BIGJPG_API_KEY
        if not key or key == 'PASTE_KEY_HERE':
            return None
        return key.strip()

    def _ffmpeg_ok(self):
        return bool(shutil.which('ffmpeg'))

    def _valid_url(self, url):
        try:
            p = urlparse(url.strip())
            return p.scheme in ('http', 'https') and bool(p.netloc)
        except Exception:
            return False

    def _extract_url(self, raw):
        found = re.search(r'https?://\S+', raw or '')
        if not found:
            return None
        trim = chr(34) + chr(39) + '“”‘’`<>()[]{}.,'
        return found.group(0).strip(trim)

    def _safe_err(self, err):
        text = str(err) or err.__class__.__name__
        text = re.sub(r'https?://\S+', '[url]', text)
        text = re.sub(r'(?i)(x-api-key|api[_-]?key|token|secret|cookie|password)[:= ]+\S+', r'\1=[hidden]', text)
        return utils.escape_html(text[:900])

    def _clean_title(self, text):
        text = re.sub(r'\s+', ' ', str(text or 'media')).strip()
        return utils.escape_html(text[:140])

    def _pick_file(self, folder):
        skip = ('.part', '.ytdl', '.json', '.tmp', '.temp')
        files = []
        for root, _, names in os.walk(folder):
            for name in names:
                path = os.path.join(root, name)
                if os.path.isfile(path) and not name.endswith(skip):
                    files.append(path)
        if not files:
            raise RuntimeError('yt-dlp ничего не сохранил')
        return max(files, key=os.path.getsize)

    def _ytdlp_opts(self, folder=None, download=True):
        opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'retries': 10,
            'fragment_retries': 10,
            'file_access_retries': 5,
            'continuedl': True,
            'windowsfilenames': True,
            'socket_timeout': 40,
            'prefer_ffmpeg': True,
            'concurrent_fragment_downloads': 4,
            'format': 'bv*+ba/bestvideo*+bestaudio/best',
            'merge_output_format': 'mkv',
            'overwrites': True,
            'restrictfilenames': False,
        }
        if folder:
            opts['outtmpl'] = os.path.join(folder, '%(title).120s [%(id)s].%(ext)s')
        if not download:
            opts['skip_download'] = True
        if self.cookie_file and os.path.exists(self.cookie_file):
            opts['cookiefile'] = self.cookie_file
        return opts

    def _download_sync(self, url, folder):
        with YoutubeDL(self._ytdlp_opts(folder=folder, download=True)) as ydl:
            info = ydl.extract_info(url, download=True)
        return info, self._pick_file(folder)

    async def _run_thread(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    async def _tt_run(self, message):
        if not message.out:
            return
        if YoutubeDL is None:
            await utils.answer(message, self.strings['no_ytdlp'])
            return

        url = self._extract_url(utils.get_args_raw(message))
        if not url:
            await utils.answer(message, self.strings['no_link'])
            return
        if not self._valid_url(url):
            await utils.answer(message, self.strings['bad_link'])
            return
        if not self._ffmpeg_ok():
            await utils.answer(message, self.strings['no_ffmpeg'])
            return

        status = await utils.answer(message, self.strings['downloading'])
        try:
            with tempfile.TemporaryDirectory(prefix='bedrock_tt_') as folder:
                info, path = await self._run_thread(self._download_sync, url, folder)
                size_mb = os.path.getsize(path) / 1024 / 1024
                if size_mb > self.max_size_mb:
                    await utils.answer(status, self.strings['too_big'].format(round(size_mb, 2), self.max_size_mb))
                    return

                title = self._clean_title(info.get('title'))
                ext = utils.escape_html(os.path.splitext(path)[1].lstrip('.') or 'file')
                caption = f"{self.strings['tt_done']}\n<code>{title}</code>\n<code>{round(size_mb, 2)} MB · {ext}</code>"

                await message.client.send_file(
                    message.chat_id,
                    path,
                    caption=caption,
                    reply_to=message.id,
                    supports_streaming=True,
                    force_document=False,
                )
            try:
                await status.delete()
            except Exception:
                pass
        except Exception as e:
            await utils.answer(status, self.strings['error'].format(self._safe_err(e)))

    async def ttcmd(self, message):
        '''.tt <url> — скачать медиа в максимальном качестве'''
        await self._tt_run(message)

    async def dlcmd(self, message):
        '''.dl <url> — alias для .tt'''
        await self._tt_run(message)

    async def ytdlcmd(self, message):
        '''.ytdl <url> — alias для .tt'''
        await self._tt_run(message)

    async def ttlimitcmd(self, message):
        '''.ttlimit <MB> — лимит размера файла'''
        if not message.out:
            return
        try:
            value = int(utils.get_args_raw(message).strip())
            if value < 1:
                raise ValueError
            self.max_size_mb = value
            await utils.answer(message, f'<b>лимит:</b> <code>{value} MB</code>')
        except Exception:
            await utils.answer(message, '<b>пример:</b> <code>.ttlimit 1900</code>')

    async def ttcookiescmd(self, message):
        '''.ttcookies <path> — cookies.txt для yt-dlp'''
        if not message.out:
            return
        path = utils.get_args_raw(message).strip().strip(chr(34) + chr(39) + '“”‘’`')
        if not path:
            self.cookie_file = None
            await utils.answer(message, '<b>cookies отключены.</b>')
            return
        if not os.path.exists(path):
            await utils.answer(message, '<b>файл не найден.</b>')
            return
        self.cookie_file = path
        await utils.answer(message, f'<b>cookies подключены:</b> <code>{utils.escape_html(path)}</code>')

    def _scale(self, token):
        t = str(token or '').lower().replace('×', 'x').strip()
        t = t.replace('scale=', '').replace('x2=', '')
        table = {
            '1': ('1', '2x'), '2': ('1', '2x'), '2x': ('1', '2x'),
            '4': ('2', '4x'), '4x': ('2', '4x'),
            '8': ('3', '8x'), '8x': ('3', '8x'),
            '16': ('4', '16x'), '16x': ('4', '16x'),
        }
        return table.get(t)

    def _parse_up(self, raw):
        style, noise, scale_api, scale_text = 'photo', '3', '1', '2x'
        clean = re.sub(r'https?://\S+', '', raw or '').strip()
        for token in clean.split():
            t = token.lower().strip()
            if t in ('photo', 'art'):
                style = t
            elif t.startswith('style=') and t.split('=', 1)[1] in ('photo', 'art'):
                style = t.split('=', 1)[1]
            elif t.startswith('noise=') and t.split('=', 1)[1] in ('-1', '0', '1', '2', '3'):
                noise = t.split('=', 1)[1]
            elif self._scale(t):
                scale_api, scale_text = self._scale(t)
            elif t in ('-1', '0', '1', '2', '3'):
                noise = t
        return style, noise, scale_api, scale_text

    async def _json(self, method, url, **kwargs):
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, url, **kwargs) as r:
                text = await r.text()
                if r.status >= 400:
                    raise RuntimeError(f'HTTP {r.status}: {text[:500]}')
                try:
                    return json.loads(text)
                except Exception:
                    return {'raw': text}

    async def _upload(self, path):
        size = os.path.getsize(path) / 1024 / 1024
        if size > self.max_upload_mb:
            raise RuntimeError(f'фото слишком большое: {round(size, 2)} MB')

        form = aiohttp.FormData()
        timeout = aiohttp.ClientTimeout(total=180)
        with open(path, 'rb') as f:
            form.add_field('file', f, filename=os.path.basename(path), content_type='application/octet-stream')
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(self.upload_url, data=form) as r:
                    text = (await r.text()).strip()
                    if r.status >= 400:
                        raise RuntimeError(f'upload HTTP {r.status}: {text[:300]}')
                    if not self._valid_url(text):
                        raise RuntimeError(f'upload не вернул ссылку: {text[:300]}')
                    return text

    async def _download_file(self, url, folder):
        name = os.path.basename(urlparse(url).path) or 'bigjpg_result.jpg'
        path = os.path.join(folder, name[:120])
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status >= 400:
                    raise RuntimeError(f'download HTTP {r.status}')
                with open(path, 'wb') as f:
                    async for chunk in r.content.iter_chunked(262144):
                        f.write(chunk)
        return path

    def _tid(self, data):
        if not isinstance(data, dict):
            return None
        for k in ('tid', 'task_id', 'id'):
            if isinstance(data.get(k), str) and data.get(k):
                return data.get(k)
        info = data.get('info')
        if isinstance(info, str) and info:
            return info
        if isinstance(info, dict):
            for k in ('tid', 'task_id', 'id'):
                if isinstance(info.get(k), str) and info.get(k):
                    return info.get(k)
        return None

    def _item(self, tid, data):
        if isinstance(data, dict) and tid in data and isinstance(data[tid], dict):
            return data[tid]
        return data if isinstance(data, dict) else {'raw': str(data)}

    def _result_url(self, item):
        if not isinstance(item, dict):
            return None
        for k in ('url', 'image', 'download_url', 'result', 'output'):
            v = item.get(k)
            if isinstance(v, str) and self._valid_url(v):
                return v
        return None

    async def _create_up(self, image_url, style, noise, scale_api):
        headers = {'X-API-KEY': self._bigjpg_key(), 'Content-Type': 'application/json'}
        payload = {'style': style, 'noise': noise, 'x2': scale_api, 'input': image_url}
        return await self._json('POST', self.bigjpg_api, headers=headers, data=json.dumps(payload))

    async def _query_up(self, tid):
        return await self._json('GET', self.bigjpg_api + tid)

    async def _retry_up(self, tid):
        return await self._json('POST', self.bigjpg_api + tid)

    async def _input_url(self, message, raw, status):
        url = self._extract_url(raw)
        if url:
            if not self._valid_url(url):
                raise RuntimeError('это не http/https ссылка')
            return url

        reply = await message.get_reply_message()
        if not reply or not reply.media:
            return None

        await utils.answer(status, self.strings['uploading'])
        with tempfile.TemporaryDirectory(prefix='bup_in_') as folder:
            path = await message.client.download_media(reply, file=folder)
            if not path or not os.path.exists(path):
                raise RuntimeError('не смог скачать фото')
            return await self._upload(path)

    async def _send_up_result(self, message, tid, item, style='photo', noise='3'):
        url = self._result_url(item)
        if not url:
            return False
        with tempfile.TemporaryDirectory(prefix='bup_out_') as folder:
            path = await self._download_file(url, folder)
            await message.client.send_file(
                message.chat_id,
                path,
                caption=self.strings['up_done'].format(style, noise) + f'\n<code>{utils.escape_html(tid)}</code>',
                reply_to=message.id,
                force_document=False,
            )
        return True

    async def _up_run(self, message):
        if not message.out:
            return
        if aiohttp is None:
            await utils.answer(message, self.strings['no_aiohttp'])
            return
        if not self._bigjpg_key():
            await utils.answer(message, self.strings['no_key'])
            return

        raw = utils.get_args_raw(message)
        style, noise, scale_api, scale_text = self._parse_up(raw)
        status = await utils.answer(message, self.strings['creating'])

        try:
            image_url = await self._input_url(message, raw, status)
            if not image_url:
                await utils.answer(status, self.strings['up_no_input'])
                return

            await utils.answer(status, self.strings['creating'])
            data = await self._create_up(image_url, style, noise, scale_api)
            tid = self._tid(data)
            if not tid:
                raise RuntimeError('Bigjpg не вернул task id: ' + json.dumps(data, ensure_ascii=False)[:500])

            await utils.answer(status, self.strings['waiting'].format(utils.escape_html(tid)))

            for _ in range(self.poll_attempts):
                await asyncio.sleep(self.poll_delay)
                item = self._item(tid, await self._query_up(tid))
                if await self._send_up_result(message, tid, item, style, noise):
                    try:
                        await status.delete()
                    except Exception:
                        pass
                    return
                state = str(item.get('status') or item.get('state') or '').lower() if isinstance(item, dict) else ''
                if state in ('error', 'failed', 'failure'):
                    raise RuntimeError(json.dumps(item, ensure_ascii=False)[:700])

            await utils.answer(status, self.strings['not_ready'].format(utils.escape_html(tid), utils.escape_html(tid)))

        except Exception as e:
            await utils.answer(status, self.strings['error'].format(self._safe_err(e)))

    async def upcmd(self, message):
        '''.up [photo|art] [2x|4x|8x|16x] [noise=3] [url] — улучшить фото через Bigjpg'''
        await self._up_run(message)

    async def bupcmd(self, message):
        '''.bup — alias для .up'''
        await self._up_run(message)

    async def uscmd(self, message):
        '''.us <task_id> — статус Bigjpg'''
        if not message.out:
            return
        if aiohttp is None:
            await utils.answer(message, self.strings['no_aiohttp'])
            return
        tid = utils.get_args_raw(message).strip()
        if not tid:
            await utils.answer(message, '<b>дай task id.</b>\n<code>.us tid</code>')
            return
        status = await utils.answer(message, '<b>проверяю...</b>')
        try:
            item = self._item(tid, await self._query_up(tid))
            if await self._send_up_result(message, tid, item):
                try:
                    await status.delete()
                except Exception:
                    pass
                return
            state = utils.escape_html(str(item.get('status') or item.get('state') or 'unknown')) if isinstance(item, dict) else 'unknown'
            raw = utils.escape_html(json.dumps(item, ensure_ascii=False)[:700])
            await utils.answer(status, f'<b>статус:</b> <code>{state}</code>\n<pre>{raw}</pre>')
        except Exception as e:
            await utils.answer(status, self.strings['error'].format(self._safe_err(e)))

    async def bstatuscmd(self, message):
        '''.bstatus — alias для .us'''
        await self.uscmd(message)

    async def urcmd(self, message):
        '''.ur <task_id> — retry Bigjpg'''
        if not message.out:
            return
        if aiohttp is None:
            await utils.answer(message, self.strings['no_aiohttp'])
            return
        tid = utils.get_args_raw(message).strip()
        if not tid:
            await utils.answer(message, '<b>дай task id.</b>\n<code>.ur tid</code>')
            return
        status = await utils.answer(message, '<b>retry...</b>')
        try:
            raw = utils.escape_html(json.dumps(await self._retry_up(tid), ensure_ascii=False)[:900])
            await utils.answer(status, f'<b>retry отправлен.</b>\n<pre>{raw}</pre>')
        except Exception as e:
            await utils.answer(status, self.strings['error'].format(self._safe_err(e)))

    async def bretrycmd(self, message):
        '''.bretry — alias для .ur'''
        await self.urcmd(message)

    async def uccmd(self, message):
        '''.uc — проверить bedrockTT'''
        if not message.out:
            return
        ytdlp = 'ok' if YoutubeDL is not None else 'missing'
        ffmpeg = 'ok' if self._ffmpeg_ok() else 'missing'
        aio = 'ok' if aiohttp is not None else 'missing'
        key = 'ok' if self._bigjpg_key() else 'missing'
        await utils.answer(message, self.strings['check'].format(ytdlp, ffmpeg, aio, key, self.max_size_mb))

    async def ttcheckcmd(self, message):
        '''.ttcheck — alias для .uc'''
        await self.uccmd(message)

    async def bupcheckcmd(self, message):
        '''.bupcheck — alias для .uc'''
        await self.uccmd(message)