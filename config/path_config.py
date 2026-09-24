import os
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    # 打包后所有可编辑资源（i18n、config.yaml 等）都放在可执行文件所在目录，
    # 不使用 PyInstaller 的临时解压目录 sys._MEIPASS。
    application_path = Path(os.path.dirname(sys.executable))
    document_path = application_path
else:
    application_path = Path(os.path.dirname(os.path.abspath(__file__)))
    application_path = application_path.joinpath("..")
    document_path = application_path

# os.environ['PYWIKIBOT_DIR'] = str(application_path.absolute())
# this will be changed later when the config is loaded
program_output_path: Path = application_path.joinpath("output")


