import os
import shutil

"""
Commands before push/on dependency updates:
    conda env export -n mds > environment.yml
    conda list --export > package-list.txt
    pip freeze > requirements.txt
"""

SETUP_PATH = "C:\\Users\\benni"


def main():
    # 1: Copy files 'environment.yml', 'requirements.txt' and 'package-list' from the given SETUP_PATH to the current directory
    copy_file("environment.yml")
    copy_file("package-list.txt")
    copy_file("requirements.txt")

    # 2: Clean 'requirements.txt' => conda will link to conda packages instead of pip packages. Get proper versions for pip packages from 'package-list.txt'.
    package_list = generate_version_dict("package-list.txt")
    requirements = generate_version_dict("requirements.txt", ["==", "@"])
    updated_requirements = change_versions(requirements, package_list)
    overwrite_requirements("requirements.txt", updated_requirements)
    overwrite_environment()


def copy_file(fileName: str, delete=True):
    """
    A function to copy and (optionally) delete a file from the given SETUP_PATH to the current directory.
    """
    try:
        filePath = os.path.join(SETUP_PATH, fileName)
        # Copy and remove the file
        shutil.copyfile(filePath, fileName)
        if delete:
            os.remove(filePath)
    except Exception as e:
        raise e
    print(f"Successfully copied file {fileName}.")


def generate_version_dict(fileName: str, separator: list[str] = ["="]) -> dict[str, str]:
    """
    A function to generate a dictionary of pip packages and their versions.
    """
    pip = {}
    with open(fileName, "r") as file:
        for line in file:
            line = line.strip()
            # two scenarios: requirements.txt has == as seperator, package-list.txt has = as seperator
            for sep in separator:
                if sep in line:
                    pkg = line.split(sep)
                    pip[pkg[0].strip()] = pkg[1].strip()
    return pip


def change_versions(pip: dict[str, str], conda: dict[str, str]):
    """
    A function to get the version of a pip package by a given conda package.

    The first value of the dict references the package name, the second value the version.
    """
    pkg: dict[str, str] = {}
    for package in pip:
        if package in conda:
            pkg[package] = conda[package]
    return pkg


def overwrite_requirements(fileName: str, content: dict[str, str]):
    """
    A function to overwrite a file with the given content.
    """
    cdn = ""
    for pkg in content:
        cdn += f"{pkg}=={content[pkg]} \n"

    with open(fileName, "w") as file:
        try:
            file.write(cdn)
        except Exception as e:
            raise e
    print("Successfully updated file.")


def overwrite_environment():
    """
    A function to overwrite the environment.yml file.

    Adds helpful comments and removes the 'prefix' key.
    """
    with open("environment.yml", "r") as file:
        lines = file.read().splitlines()

    # Remove prefix
    lines = [line for line in lines if "prefix" not in line]

    # Add comments
    lines.insert(0, "# This file may be used to create an environment using:")
    lines.insert(1, "# conda env create -f environment.yml")
    lines.insert(2, "# Note: platform is win-64")

    with open("environment.yml", "w") as file:
        try:
            file.write("\n".join(lines))
        except Exception as e:
            raise e
    print("Successfully updated file environment.yml.")


if __name__ == "__main__":
    main()
