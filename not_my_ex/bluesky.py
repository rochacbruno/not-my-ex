from asyncio import gather
from datetime import datetime
from re import compile

from backoff import expo, on_exception
from httpx import ReadTimeout

from not_my_ex import settings

URL = compile(
    r"(http(s?):\/\/([\w_-]+(?:(?:\.[\w_-]+)+))([\w.,@?^=%&:\/~+#-]*[\w@?^=%&\/~+#-]))"
)


class BlueskyCredentialsNotFoundError(Exception):
    pass


class BlueskyError(Exception):
    def __init__(self, response, *args, **kwargs):
        data = response.json()
        msg = (
            f"Error from Bluesky agent/instance - "
            f"[HTTP Status {response.status_code}] "
            f"{data['error']}: {data['message']}"
        )
        super().__init__(msg, *args, **kwargs)


class Bluesky:
    def __init__(self, client):
        if settings.BLUESKY not in settings.CLIENTS_AVAILABLE:
            raise BlueskyCredentialsNotFoundError(
                "NOT_MY_EX_BSKY_EMAIL and/or NOT_MY_EX_BSKY_PASSWORD "
                "environment variables not set"
            )

        self.client = client
        self.credentials = {
            "identifier": settings.BSKY_EMAIL,
            "password": settings.BSKY_PASSWORD,
        }
        self.token, self.did, self.handle = None, None, None

    async def auth(self):
        resp = await self.client.post(
            f"{settings.BSKY_AGENT}/xrpc/com.atproto.server.createSession",
            json=self.credentials,
        )
        if resp.status_code != 200:
            raise BlueskyError(resp)

        data = resp.json()
        self.token, self.did, self.handle = (
            data["accessJwt"],
            data["did"],
            data["handle"],
        )

    @on_exception(expo, ReadTimeout, max_tries=7)
    async def xrpc(self, resource, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.token}"
        url = f"{settings.BSKY_AGENT}/xrpc/{resource}"
        return await self.client.post(url, headers=headers, **kwargs)

    async def upload(self, media):
        resp = await self.xrpc(
            "com.atproto.repo.uploadBlob",
            headers={"Content-type": media.mime},
            data=media.content,
        )
        if resp.status_code != 200:
            BlueskyError(resp)

        data = resp.json()
        return {"alt": media.alt, "image": data["blob"]}

    async def data(self, post):
        data = {
            "repo": self.did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": post.text,
                "createdAt": datetime.utcnow().isoformat(),
                "langs": [post.lang],
            },
        }

        if matches := URL.findall(post.text):
            data["record"]["facets"] = []
            start = 0
            source = post.text.encode()
            for url, *_ in matches:
                target = url.encode()
                start = source.find(target, start)
                end = start + len(target)
                data["record"]["facets"].append(
                    {
                        "index": {"byteStart": start, "byteEnd": end},
                        "features": [
                            {
                                "$type": "app.bsky.richtext.facet#link",
                                "uri": url,
                            }
                        ],
                    }
                )
                start = end

        if post.media:
            uploads = tuple(self.upload(media) for media in post.media)
            embed = await gather(*uploads)
            data["record"]["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": embed,
            }

        return data

    async def url_from(self, resp):
        data = resp.json()
        *_, post_id = data["uri"].split("/")
        return f"https://bsky.app/profile/{self.handle}/post/{post_id}"

    async def post(self, post):
        data = await self.data(post)
        resp = await self.xrpc("com.atproto.repo.createRecord", json=data)
        if resp.status_code != 200:
            raise BlueskyError(resp)

        return await self.url_from(resp)
