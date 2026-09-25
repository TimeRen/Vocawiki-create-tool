from types import SimpleNamespace

from main import create_lyrics
from models.song import get_manual_lyrics


def main():
    lyrics = get_manual_lyrics()
    print(create_lyrics(SimpleNamespace(lyrics=lyrics, color_editing=None)))


if __name__ == "__main__":
    main()
