from .common import InfoExtractor
from ..utils import (
    int_or_none,
    parse_iso8601,
    srt_subtitles_timecode,
    traverse_obj,
    update_url_query,
    url_or_none,
)


class MnetPlusBaseIE(InfoExtractor):
    def _get_auth_headers(self, url):
        cookies = self._get_cookies(url)
        bearer_token = traverse_obj(cookies, ('_mnet_atk', 'value'))
        unique_id = traverse_obj(cookies, ('mnet-unique-id', 'value'))
        headers = {}
        if bearer_token:
            headers['Authorization'] = f'Bearer {bearer_token}'
        if unique_id:
            headers['x-lang-country'] = 'en:US'
            headers['x-user-agent'] = f'en:US::WEB:Chrome:::{unique_id}'
        return headers or None

    def _set_cloudfront_cookies(self, video_domain, cloudfront_response):
        for key in ('policy', 'signature', 'keyPairId'):
            value = traverse_obj(cloudfront_response, (key, {str})) or ''
            cookie_name, sep, cookie_value = value.partition('=')
            if sep:
                self._set_cookie(video_domain, cookie_name, cookie_value)

    def _get_subtitles(self, captions_domain, video_id, caption_id, duration, lang_configs, headers):
        return self._fetch_captions(
            captions_domain, video_id, caption_id, duration, lang_configs, headers,
            ai_only=False, write_param='writesubtitles')

    def _get_automatic_captions(self, captions_domain, video_id, caption_id, duration, lang_configs, headers):
        return self._fetch_captions(
            captions_domain, video_id, caption_id, duration, lang_configs, headers,
            ai_only=True, write_param='writeautomaticsub')

    def _fetch_captions(self, captions_domain, video_id, caption_id, duration, lang_configs, headers, ai_only, write_param):
        # Mnet Plus seems to have two subtitle systems
        # - Standard subtitle stream approach baked into the HLS streams
        # - A custom JSON API-based system, on the /cue endpoint for videos and the
        #   /contents endpoint for livestreams, that reports subs in JSON.
        #
        # The HLS based subs get added in _extract_m3u8_formats_and_subtitles in the
        # extractors below, the custom subs get added here, via the _fetch_cues_to_srt helper.
        if not caption_id:
            return {}

        subtitles = {}
        for lang_config in traverse_obj(lang_configs, (..., {dict})):
            language_code = lang_config.get('language')
            if not language_code:
                continue

            is_ai = bool(lang_config.get('aiGeneratedLabel'))
            if ai_only != is_ai:
                continue

            # This is reached for --list-subs as well as --write-subs/--write-auto-subs.
            # Only fetch the actual cues for the latter, because mnet's custom subtitle
            # system needs an api request every 15 seconds of video and is thus rather slow.
            if not self.get_param(write_param):
                subtitles.setdefault(language_code, []).append({'ext': 'srt', 'name': 'mnet custom subtitles'})
                continue

            srt_content = self._fetch_cues_to_srt(captions_domain, video_id, caption_id, language_code, duration, headers)
            if srt_content:
                subtitles.setdefault(language_code, []).append({'ext': 'srt', 'name': 'mnet custom subtitles', 'data': srt_content})

        return subtitles

    def _fetch_cues_to_srt(self, captions_url, video_id, caption_id, language, duration, headers):
        # parsing custom subs
        # - make request to subtitle endpoint with a start offset of 0
        # - parse subs in content_map json field
        # - update start offset using captionIntervalSecond json field
        # - repeat until the reported duration is reached, or until the api runs out of cues
        # This is what the javascript seems to be doing too. I don't know if it's possible to get all subs at once.
        cues = []
        offset = 0
        caption_interval = None

        while duration is None or offset < duration:
            cues_url = update_url_query(
                captions_url.format(video_id=video_id, caption_id=caption_id),
                {'language': language, 'displaySecond': offset})

            cues_data = self._download_json(
                cues_url, video_id,
                note=f'Downloading {language} subtitles (offset {offset})',
                errnote=f'Failed to download {language} subtitles',
                headers=headers, fatal=False)
            if not cues_data:
                break

            content_map = cues_data.get('contentMap') or {}
            if not content_map:
                break

            if caption_interval is None:
                caption_interval = int_or_none(cues_data.get('captionIntervalSecond'))
                # A missing or non-positive interval would never advance the offset
                if not caption_interval or caption_interval < 0:
                    break

            for cue_key in sorted(content_map.keys(), key=int):
                cue = content_map[cue_key]
                start = int_or_none(cue.get('displaySecond'))
                dur = int_or_none(cue.get('displayDurationSecond'))
                text = cue.get('content')
                if start is not None and dur is not None and text:
                    cues.append({'start': start, 'duration': dur, 'text': text})

            offset += caption_interval

        if not cues:
            return None

        # convert to srt
        srt = ''
        for i, cue in enumerate(cues, 1):
            srt += f'{i}\n{srt_subtitles_timecode(cue["start"])} --> {srt_subtitles_timecode(cue["start"] + cue["duration"])}\n{cue["text"]}\n\n'
        return srt


class MnetPlusVideoIE(MnetPlusBaseIE):
    _VALID_URL = r'https?://(?:www\.)?mnetplus\.world/media/[a-zA-Z-]+/videos/(?P<id>[0-9a-f]+)'
    _API_DOMAIN = 'https://api.mnetplus.world/media/v1/public/guests/videos/{video_id}'
    _COOKIES_DOMAIN = 'https://api.mnetplus.world/media/v2/public/videos/{video_id}/cookies'
    _CAPTIONS_DOMAIN = 'https://api.mnetplus.world/media/v1/public/videos/{video_id}/captions/{caption_id}/cues'
    _VIDEO_DOMAIN = 'video.cdn.mnetplus.world'
    _TESTS = [{
        'url': 'https://www.mnetplus.world/media/en/videos/69f82e651511b17e55000f2a',
        'info_dict': {
            'id': '69f82e651511b17e55000f2a',
            'ext': 'mp4',
            'title': "KATSEYE 'PINKY UP' (4K) | STUDIO CHOOM ORIGINAL",
            'description': 'md5:c4815190e5f4499d764d4e105ff5384f',
            'thumbnail': r're:https://image\.cdn\.mnetplus\.world/.*',
            'upload_date': '20260506',
            'timestamp': 1778035913,
            'duration': 162,
            'view_count': int,
            'like_count': int,
            'comment_count': int,
            'tags': [],
        },
        'params': {
            'skip_download': True,
        },
        'expected_warnings': [r'Only extracting preview quality'],
    }, {
        'url': 'https://www.mnetplus.world/media/en/videos/69f82e651511b17e55000f2a',
        'info_dict': {
            'id': '69f82e651511b17e55000f2a',
            'ext': 'mp4',
            'title': "KATSEYE 'PINKY UP' (4K) | STUDIO CHOOM ORIGINAL",
            'description': 'md5:c4815190e5f4499d764d4e105ff5384f',
            'thumbnail': r're:https://image\.cdn\.mnetplus\.world/.*',
            'upload_date': '20260506',
            'timestamp': 1778035913,
            'duration': 162,
            'view_count': int,
            'like_count': int,
            'comment_count': int,
            'tags': [],
            'formats': 'mincount:6',
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Requires authentication (cookies) for 4K quality',
    }, {
        'url': 'https://www.mnetplus.world/media/en/videos/695a57b0c5b5d509fb4888c1',
        'info_dict': {
            'id': '695a57b0c5b5d509fb4888c1',
            'ext': 'mp4',
            'title': '(SUB) ALPHA DRIVE ONE DEBUT SHOW THE FIRST ALARM | Teaser',
            'description': 'md5:fcd6433254085f34e31b70b338ce8239',
            'thumbnail': r're:https://image\.cdn\.mnetplus\.world/.*',
            'upload_date': '20260107',
            'timestamp': 1767769200,
            'duration': 61,
            'view_count': int,
            'like_count': int,
            'comment_count': int,
            'tags': [],
            'subtitles': {
                'eng': 'mincount:1',
                'kor': 'mincount:1',
                'jpn': 'mincount:1',
                'zho': 'mincount:1',
                'zha': 'mincount:1',
                'spa': 'mincount:1',
                'ind': 'mincount:1',
                'tha': 'mincount:1',
            },
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Requires authentication for subs',
    }, {
        'url': 'https://www.mnetplus.world/media/en/videos/69eec9721d39e70911e5ad2c',
        'info_dict': {
            'id': '69eec9721d39e70911e5ad2c',
            'ext': 'mp4',
            'title': '(SUB) [Tingle Interview] ASMR ver. Q&A by TWS SHINYU & DOHOON',
            'description': 'md5:5c335f76c0fc556599681f21b0a03b6b',
            'thumbnail': r're:https://image\.cdn\.mnetplus\.world/.*',
            'upload_date': '20260427',
            'timestamp': 1777280400,
            'duration': 2562,
            'view_count': int,
            'like_count': int,
            'comment_count': int,
            'tags': ['ASMR', '팅글', '팅글시리즈'],
            'subtitles': {
                'en': 'mincount:1',
                'ja': 'mincount:1',
            },
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Requires authentication for subs',
    }, {
        'url': 'https://www.mnetplus.world/media/en/videos/69f84e9b1511b17e55001e7b',
        'info_dict': {
            'id': '69f84e9b1511b17e55001e7b',
            'ext': 'mp4',
            'title': "[Clip] 'The magical healer? 𝐈𝐭'𝐬 𝐦𝐞' IROHA will heal everything! | Tingle Room",
            'description': 'md5:4eb942250e09402e970cd15f738402a8',
            'thumbnail': r're:https://image\.cdn\.mnetplus\.world/.*',
            'upload_date': '20260511',
            'timestamp': 1778493600,
            'duration': 191,
            'view_count': int,
            'like_count': int,
            'comment_count': int,
            'tags': ['ASMR', '팅글', '팅글시리즈'],
            'automatic_captions': {
                'ko': 'mincount:1',
                'en': 'mincount:1',
                'ja': 'mincount:1',
                'zh_CN': 'mincount:1',
                'zh_TW': 'mincount:1',
            },
        },
        'params': {
            'skip_download': True,
        },
        'skip': 'Requires authentication for subs',
    }, {
        'url': 'https://mnetplus.world/media/ko/videos/69f82e651511b17e55000f2a',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        headers = self._get_auth_headers(url)

        video_json = self._download_json(
            self._API_DOMAIN.format(video_id=video_id), video_id,
            errnote='Failed to get video information', headers=headers)

        geo_block = traverse_obj(video_json, ('geoBlock', {dict})) or {}
        if geo_block.get('isBlocked'):
            self.raise_geo_restricted(
                geo_block.get('blockedMessage') or 'This video is not available in your region')

        duration = int_or_none(video_json.get('videoLength'), scale=1000)

        formats, hls_subtitles = [], {}
        video_master_url = traverse_obj(video_json, ('videoUrl', {url_or_none}))
        if not video_master_url:
            self.raise_no_formats('No video master URL found in API response', expected=True)
        else:
            video_master_url = update_url_query(video_master_url, {'maxResolution': None})

            # Authenticated users get a m3u8 master url with /converted/.
            # Non-authenticated users get a m3u8 master url with /preview/ that only has
            # a few seconds of playback at low quality.
            if '/converted/' in video_master_url:
                cloudfront = self._download_json(
                    self._COOKIES_DOMAIN.format(video_id=video_id), video_id,
                    errnote='Failed to download per-video cookie credentials',
                    data=b'', headers=headers)
                self._set_cloudfront_cookies(self._VIDEO_DOMAIN, cloudfront)
            else:
                self.report_warning(
                    'Only extracting preview quality and length. '
                    f'Full quality requires authentication. {self._login_hint("cookies")}')

            formats, hls_subtitles = self._extract_m3u8_formats_and_subtitles(
                video_master_url, video_id, 'mp4', m3u8_id='hls', fatal=False)

        video_caption = traverse_obj(video_json, ('videoCaption', {dict})) or {}
        caption_id = video_caption.get('videoCaptionId')
        lang_configs = video_caption.get('languageConfigs')
        api_subtitles = self.extract_subtitles(
            self._CAPTIONS_DOMAIN, video_id, caption_id, duration, lang_configs, headers)
        automatic_captions = self.extract_automatic_captions(
            self._CAPTIONS_DOMAIN, video_id, caption_id, duration, lang_configs, headers)

        return {
            'id': video_id,
            'duration': duration,
            'formats': formats,
            'subtitles': self._merge_subtitles(hls_subtitles, api_subtitles),
            'automatic_captions': automatic_captions,
            'tags': [tag.removeprefix('#') for tag in traverse_obj(video_json, ('tags', ..., {str}))],
            'http_headers': {'Referer': 'https://www.mnetplus.world/'},
            **traverse_obj(video_json, {
                'title': ('name', {str}),
                'description': ('description', {str}),
                'thumbnail': ('thumbnailUrl', {url_or_none}),
                'timestamp': ('startAt', {parse_iso8601}),
                'view_count': ('viewCount', {int_or_none}),
                'like_count': ('likeCount', {int_or_none}),
                'comment_count': ('commentCount', {int_or_none}),
            }),
        }


class MnetPlusLiveIE(MnetPlusBaseIE):
    _VALID_URL = r'https?://(?:www\.)?mnetplus\.world/media/[a-zA-Z-]+/lives/(?P<id>[0-9a-f]+)'
    _API_DOMAIN = 'https://api.mnetplus.world/media/v1/public/lives/{video_id}/trace?drmType=NONE'
    _LIVE_INFO_DOMAIN = 'https://api.mnetplus.world/media/v1/public/guests/lives/{video_id}'
    _COOKIES_DOMAIN = 'https://api.mnetplus.world/media/v2/public/lives/{video_id}/cookies'
    _VIDEO_DOMAIN = 'live.cdn.mnetplus.world'
    _TESTS = [{
        # Live broadcasts are only available while they are on air
        'url': 'https://www.mnetplus.world/media/en/lives/6979a2f4b78a3d0e2a6f1c85',
        'only_matching': True,
    }, {
        'url': 'https://mnetplus.world/media/ko/lives/6979a2f4b78a3d0e2a6f1c85',
        'only_matching': True,
    }]

    def _real_extract(self, url):
        video_id = self._match_id(url)
        headers = self._get_auth_headers(url)

        live_info = self._download_json(
            self._LIVE_INFO_DOMAIN.format(video_id=video_id), video_id,
            errnote='Failed to get live stream metadata', headers=headers)

        timestamp = parse_iso8601(live_info.get('liveStartAt'))
        live_end = parse_iso8601(live_info.get('liveEndAt'))

        video_json = {}
        formats, subtitles = [], {}
        if live_info.get('status') == 'ARCHIVE':
            self.raise_no_formats('This live stream has ended and is no longer available', expected=True)
        else:
            video_json = self._download_json(
                self._API_DOMAIN.format(video_id=video_id), video_id,
                errnote='Failed to get live stream information',
                data=b'', headers=headers)

            geo_block = traverse_obj(video_json, ('geoBlock', {dict})) or {}
            if geo_block.get('isBlocked'):
                self.raise_geo_restricted(
                    geo_block.get('blockedMessage') or 'This stream is not available in your region')

            live_url = traverse_obj(video_json, ('liveUrl', {url_or_none}))
            if not live_url:
                self.raise_no_formats('No live stream URL found in API response', expected=True)
            else:
                cloudfront = self._download_json(
                    self._COOKIES_DOMAIN.format(video_id=video_id), video_id,
                    errnote='Failed to download per-stream cookie credentials',
                    data=b'', headers=headers)
                self._set_cloudfront_cookies(self._VIDEO_DOMAIN, cloudfront)

                formats, subtitles = self._extract_m3u8_formats_and_subtitles(
                    update_url_query(live_url, {'maxResolution': None}),
                    video_id, 'mp4', m3u8_id='hls', fatal=False)

        return {
            'id': video_id,
            'title': traverse_obj(live_info, ('name', 'en', {str}), ('name', {str})),
            'timestamp': timestamp,
            'duration': live_end - timestamp if timestamp and live_end else None,
            'formats': formats,
            'subtitles': subtitles,
            'is_live': video_json.get('status') == 'ON_AIR',
            'http_headers': {'Referer': 'https://www.mnetplus.world/'},
            **traverse_obj(video_json, {
                'description': ('description', {str}),
                'thumbnail': ('thumbnailUrl', {url_or_none}),
                'view_count': ('viewCount', {int_or_none}),
            }),
        }
