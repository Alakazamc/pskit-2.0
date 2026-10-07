"""Check saved image thumbnails and separate text bubbles in the built UI."""

import argparse
import asyncio
import json
import struct
import zlib
from itertools import product
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect


def image_fixture():
    def chunk(kind, raw):
        return struct.pack('!I', len(raw)) + kind + raw + struct.pack(
            '!I', zlib.crc32(kind + raw) & 0xFFFFFFFF,
        )

    rows = b''.join(b'\0' + bytes([80 + row // 2, 130, 180]) * 160 for row in range(120))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', 160, 120, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


async def check(base_url, executable, screenshots):
    screenshots.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable,
                                                   args=['--no-sandbox'])
        for language, theme, width in product(('en', 'zh'), ('dark', 'light'), (1360, 390)):
            context = await browser.new_context(viewport={'width': width, 'height': 860})
            await context.add_init_script(
                "localStorage.setItem('research_access_token','test-only');"
                f"localStorage.setItem('research_language','{language}');"
                f"localStorage.setItem('pskit-theme','{theme}');"
            )
            page = await context.new_page()
            errors = []
            seen = []
            page.on('pageerror', lambda error, errors=errors: errors.append(str(error)))
            ready, state = asyncio.Event(), {'allow_retry': False}
            identity = {'id': 'browser-user', 'name': 'Browser test',
                        'email': 'browser@example.invalid', 'is_anonymous': False}

            async def api(route, _request=None, *, ready=ready, state=state, identity=identity, seen=seen):
                path = urlsplit(route.request.url).path.split('/api/v1', 1)[1]
                seen.append(path)
                value = []
                if path == '/auth/csrf':
                    value = {'csrf_token': 'test-only-csrf'}
                elif path == '/auth/refresh':
                    value = {'access_token': 'test-only', 'expires_in': 3600, 'user': identity}
                elif path == '/me':
                    value = identity
                elif path == '/c':
                    value = [{'id': 'image-chat', 'project_id': 'project-browser-user',
                              'title': 'Image review', 'status': 'completed'}]
                elif path == '/models':
                    value = [{'id': 'vision-model', 'supports_images': True}]
                elif path.endswith('/messages'):
                    value = [{
                        'id': 'message-1', 'session_id': 'image-chat', 'role': 'user',
                        'created_at': '2026-10-07T06:18:00Z', 'parts': [
                            {'type': 'text', 'text': 'Describe this image'},
                            {'type': 'file', 'id': 'ready-image', 'name': 'image.png'},
                        ],
                    }, {
                        'id': 'message-2', 'session_id': 'image-chat', 'role': 'user',
                        'created_at': '2026-10-07T06:19:00Z', 'parts': [
                            {'type': 'text', 'text': 'Review all ten images'},
                            *[{'type': 'file', 'id': f'image-{index}', 'name': 'image.png'}
                              for index in range(10)],
                        ],
                    }]
                elif path.endswith('/download'):
                    if 'ready-image' in path:
                        await ready.wait()
                    elif 'image-9/' in path and not state['allow_retry']:
                        await route.fulfill(status=503, content_type='application/json',
                                            body=json.dumps({'detail': 'Unavailable'}))
                        return
                    await route.fulfill(content_type='image/png', body=image_fixture())
                    return
                await route.fulfill(content_type='application/json', body=json.dumps(value))

            await page.route('**/api/v1/**', api)
            await page.goto(base_url.rstrip('/') + '/session/image-chat')
            first = page.locator('.message-row.user').first
            loading_name = 'Loading image image.png' if language == 'en' else '正在加载图片 image.png'
            unavailable = 'Image unavailable' if language == 'en' else '图片暂时无法显示'
            retry_name = 'Retry image image.png' if language == 'en' else '重新加载图片 image.png'
            attachments_name = 'Attachments' if language == 'en' else '附件'
            try:
                await expect(first.get_by_role('status', name=loading_name)).to_be_visible()
            except AssertionError:
                print({'requests': seen, 'errors': errors,
                       'body': (await page.locator('body').inner_text())[:1200]})
                raise
            await expect(first.get_by_text('Describe this image')).to_be_visible()
            ready.set()
            await expect(first.get_by_role('img', name='image.png')).to_be_visible()
            picture = await first.get_by_role('img', name='image.png').bounding_box()
            bubble = await first.locator('.message-body').bounding_box()
            assert picture['y'] + picture['height'] <= bubble['y'], (picture, bubble)
            assert abs(picture['x'] + picture['width'] - bubble['x'] - bubble['width']) < 2
            await expect(first.locator('.message-body img')).to_have_count(0)
            last = page.locator('.message-row.user').last
            await expect(last.get_by_text(unavailable)).to_be_visible()
            state['allow_retry'] = True
            await last.get_by_role('button', name=retry_name).click()
            await expect(last.get_by_role('img', name='image.png')).to_have_count(10)
            region = last.get_by_role('region', name=attachments_name)
            dimensions = await region.evaluate('(el) => ({height:el.clientHeight, scroll:el.scrollHeight})')
            assert dimensions['height'] <= 320 and dimensions['scroll'] > dimensions['height']
            await region.focus()
            await expect(region).to_be_focused()
            await page.keyboard.press('End')
            await expect(last.get_by_text('Review all ten images')).to_be_visible()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            await page.screenshot(path=str(screenshots / f'{language}-{theme}-{width}.png'))
            assert not errors, errors
            print(f'PASS {language} {theme} {width}: image above text, right alignment, pending, retry, ten-image scroll')
            await context.close()
        await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:4176')
    parser.add_argument('--browser-executable')
    parser.add_argument('--screenshots', type=Path, default=Path('/tmp/pskit-message-images'))
    args = parser.parse_args()
    asyncio.run(check(args.base_url, args.browser_executable, args.screenshots))
