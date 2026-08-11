-- Use folder name to build extension name and tag. Version is specified explicitly.
local ext = get_current_extension_info()

project_ext (ext)

-- Link only those files and folders into the extension target directory
repo_build.prebuild_link {
    { "docs", ext.target_dir.."/docs" },
    { "twin", ext.target_dir.."/twin" },
}
