--[==========================================[--
          L3BUILD FILE FOR ELEGANTBOOK
     Check PDF File & Directory After Build
--]==========================================]--

--[==========================================[--
                Basic Information
             Do Check Before Upload
--]==========================================]--
module           = "elegantbook"
version          = "4.7 2026-05-01"
maintainer       = "Ran Wang"
uploader         = maintainer
maintainid       = "ElegantLaTeX"
email            = "ranwang.osbert@outlook.com"
repository       = "https://github.com/" .. maintainid .. "/" .. module
announcement     = ""
note             = ""
summary          = "Elegant LaTeX Template for Books"
description      = [[ElegantBook is designed for writing Books. This template is based on the standard LaTeX book class. The goal of this template is to make the writing process more elegant.]]

--[==========================================[--
         Build, Pack and Upload To CTAN
         Do not Modify Unless Necessary
--]==========================================]--
ctanzip          = module
excludefiles     = {"*~"}
textfiles        = {"*.md", "LICENSE", "*.lua", "*.cls", "*.bib", "*.tex"}
typesetexe       = "latexmk -pdf"
typesetfiles     = {module .. "-cn.tex", module .. "-en.tex"}
typesetopts      = "-interaction=nonstopmode"
typesetruns      = 1
typesetsuppfiles = {"*.cls", "*.bib"}
imagesuppdir     = "image"
figuresuppdir    = "figure"
specialtypesetting = specialtypesetting or {}
specialtypesetting[module .. "-cn.tex"] = {cmd = "latexmk -pdfxe"}
binaryfiles      = {"*.png", "*.jpg", "*.pdf"}
sourcefiles      = {"*.cls", "*.bib"}
docfiles         = {"*.pdf", "*.md", "LICENSE", module .. "-cn.tex", module .. "-en.tex"}

uploadconfig = {
  pkg          = module,
  version      = version,
  author       = maintainer,
  uploader     = uploader,
  email        = email,
  summary      = summary,
  description  = description,
  announcement = announcement,
  note         = note,
  license      = "lppl1.3c",
  ctanPath     = "/macros/latex/contrib/" .. module .. "/",
  support      = repository .. "/issues",
  bugtracker   = repository .. "/issues",
  repository   = repository,
  development  = "https://github.com/" .. maintainid,
  update       = true
}

--[==========================================[--
         Custom Hooks for Directory Structure
--]==========================================]--

function tex(file, dir, cmd)
  dir = dir or "."
  cmd = cmd or typesetexe .. " " .. typesetopts
  return run(dir, cmd .. " " .. file)
end

function docinit_hook()
  for _, glob in pairs(typesetsuppfiles) do
    cp(glob, currentdir, typesetdir)
  end

  for _, subdir in pairs({imagesuppdir, figuresuppdir}) do
    local dest = typesetdir .. "/" .. subdir
    mkdir(dest)
    cp("*", subdir, dest)
  end

  for _, texfile in pairs(typesetfiles) do
    cp(texfile, currentdir, typesetdir)
  end
  return 0
end

function copyctan()
  local target = ctandir .. "/" .. ctanpkg
  mkdir(target)
  
  for _, f in ipairs(textfiles) do
    cp(f, ".", target)
  end

  cp("*.pdf", typesetdir, target)

  mkdir(target .. "/" .. figuresuppdir)
  cp("*", figuresuppdir, target .. "/" .. figuresuppdir)

  mkdir(target .. "/" .. imagesuppdir)
  cp("*", imagesuppdir, target .. "/" .. imagesuppdir)

  return 0
end