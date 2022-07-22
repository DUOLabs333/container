#mod-download requests -d modules
mod clean
(cd modules; mod clean)
rm -rf modules/_utils.py
mod-convert modules/_utils modules/_utils.py
mod build --make-script container.py
