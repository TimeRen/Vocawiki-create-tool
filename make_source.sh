rm -rf dist
mkdir dist
cp -r config i18n models utils main.py parse_lyrics.py process_image.py README.md config_simple.yaml *.html wiki_credentials.yaml requirements.txt dist
mv dist/config_simple.yaml dist/config.yaml
python compile_mo.py dist/i18n
tar -czf macOS.zip dist
