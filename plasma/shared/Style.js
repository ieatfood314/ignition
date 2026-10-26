.pragma library
// The look shared by every ignition widget: Nothing OS's dot-matrix and mono typefaces (the same ones the
// Android widgets use), the theme's text colour for everything, and one sparse red accent.

const families = Qt.fontFamilies();

function pick(names, fallback) {
    for (const n of names) {
        if (families.indexOf(n) >= 0)
            return n;
    }
    return fallback;
}

const dot = pick(["Ndot 57", "Ndot 55", "Doto"], "monospace");
const dotCaps = pick(["Ndot57Caps", "Ndot55Caps", "Ndot 57", "Doto"], "monospace");
const mono = pick(["Lettera Mono LL", "Space Mono", "JetBrains Mono", "Hack", "DejaVu Sans Mono"], "monospace");

const red = "#D71921";
const secondary = 0.55;
const dim = 0.18;

function alpha(c, a) {
    return Qt.rgba(c.r, c.g, c.b, a);
}
