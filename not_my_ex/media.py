from dataclasses import dataclass
from typing import Optional

from aiofiles import open, os

from not_my_ex.mime import mime_for


@dataclass
class Media:
    path: Optional[str]
    content: bytes
    mime: str
    alt: Optional[str] = None

    @classmethod
    async def from_img(cls, img: str, alt: Optional[str] = None) -> "Media":
        if not await os.path.exists(img):
            raise ValueError(f"File {img} does not exist")

        async with open(img, "rb") as handler:
            contents = await handler.read()

        mime = mime_for(img, contents)
        if not isinstance(mime, str):
            raise ValueError(f"Could not guess mime type for {img}")

        return cls(img, contents, mime, alt)

    def check_alt_text(self):
        while self.path and not self.alt:
            alt = input(f"Enter an alt text for {self.path}: ")
            self.alt = alt.strip() or None
