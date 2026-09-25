import dataclasses


@dataclasses.dataclass
class Color:
    red: int
    green: int
    blue: int

    def __getitem__(self, item: int):
        if item == 0:
            return self.red
        if item == 1:
            return self.green
        if item == 2:
            return self.blue
        raise IndexError("A color only has length 3.")

    def perceived_lightness(self) -> int:
        vR = self.red / 255
        vG = self.green / 255
        vB = self.blue / 255

        def rgb_to_linear(color_channel):
            if color_channel <= 0.04045:
                return color_channel / 12.92
            else:
                return pow(((color_channel + 0.055) / 1.055), 2.4)

        Y = (0.2126 * rgb_to_linear(vR) +
             0.7152 * rgb_to_linear(vG) +
             0.0722 * rgb_to_linear(vB))
        if Y <= (216 / 24389):
            ans = Y * (24389 / 27)
        else:
            ans = pow(Y, (1 / 3)) * 116 - 16
        return round(ans)

    def to_hex(self) -> str:
        return '#%02x%02x%02x' % (self.red, self.green, self.blue)


def black():
    return Color(0, 0, 0)


def white():
    return Color(255, 255, 255)


def get_text_color(c: Color) -> Color:
    white = Color(255, 255, 255)
    num = c.perceived_lightness()
    if num > white.perceived_lightness() / 2:
        return Color(0, 0, 0)
    return white


@dataclasses.dataclass
class ColorScheme:
    text: Color
    background: Color = None


@dataclasses.dataclass
class ColorEditing:
    """颜色编辑器产出的各模板样式；空字符串表示未设置，沿用生成时的默认值。

    除 songbox 外，各字段存的都是「按该模板语法写好的值」，可能含多条 CSS 声明：
      lbgcolor      -> background-color: 的值（模板自带属性名前缀），如 "#000; border-radius: 4px"
      ltcolor       -> color 的值
      rbdcolor      -> 列表格边框色（标签格带额外声明时补上，避免模板里 border: <lbgcolor> 被写坏）
      lstyle/rstyle -> 直接作为 CSS 文本传给 LyricsKai（如 "color: #c00; font-size: 15px;"）
      containerstyle -> 同上，作用于整个歌词容器
    """
    songbox: str = ""            # VOCALOID_Songbox 的 |颜色1/2/3 参数块（原样插入）
    introduction_bg: str = ""    # VOCALOID Songbox Introduction 的 |lbgcolor
    introduction_fg: str = ""    # VOCALOID Songbox Introduction 的 |ltcolor
    introduction_border: str = ""  # VOCALOID Songbox Introduction 的 |rbdcolor
    lyrics_original: str = ""    # LyricsKai 的 |lstyle
    lyrics_translated: str = ""  # LyricsKai 的 |rstyle
    lyrics_background: str = ""  # LyricsKai 的 |containerstyle
