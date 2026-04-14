#!/usr/bin/env python3
"""
Hello World script for the hello-world skill
"""
import sys


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "World"
    print(f"Hello, {name}! Welcome to Agent Skills.")


if __name__ == "__main__":
    main()
