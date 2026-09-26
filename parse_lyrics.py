"""单独打开主窗口的「歌词」页，并打印生成的歌词段落。

图形界面不可用时（没装 PyQt5 或加了 --console）会在终端里跑，这时拿不到界面上的输入，
只会打印一段空歌词 —— 这个脚本本来就是配合界面整理歌词用的。
"""
from types import SimpleNamespace

from config.config import application_path, load_config
from main import create_lyrics
from models.song import get_manual_lyrics
from utils import ui


def generate():
    load_config(application_path.joinpath("config.yaml"))
    lyrics = get_manual_lyrics()
    print(create_lyrics(SimpleNamespace(lyrics=lyrics, color_editing=None)))
    return None


def main():
    ui.run(generate, title="Vocawiki 歌词整理")


if __name__ == "__main__":
    main()
