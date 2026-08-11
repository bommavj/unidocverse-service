import os
import glob

def main():
    paths = [
        "/Users/bommavj/Library/Application Support/UniDocVerse",
        "/Users/bommavj/Library/Application Support/com.unidocverse.desktop",
        "/Users/bommavj/Documents/unidocverse/unidocverse-service/storage/logs"
    ]
    
    print("Searching for log files:")
    for path in paths:
        if os.path.exists(path):
            print(f"Directory exists: {path}")
            # recursively find log files
            log_files = glob.glob(os.path.join(path, "**", "*.log"), recursive=True)
            log_files += glob.glob(os.path.join(path, "*.log"), recursive=True)
            log_files += glob.glob(os.path.join(path, "**", "*.txt"), recursive=True)
            log_files = list(set(log_files))
            for lf in log_files:
                print(f"  Found log file: {lf} ({os.path.getsize(lf)} bytes)")
                # Check if it has keyword
                try:
                    with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        if "Vijay Bomma" in content or "ShoonyaTax" in content:
                            print(f"    -> Mentions Vijay Bomma or ShoonyaTax!")
                            # print last 20 lines matching the document
                            lines = content.splitlines()
                            matching_lines = [l for l in lines if "8026afa5" in l or "Vijay" in l or "Shoonya" in l or "error" in l.lower()]
                            print(f"    -> Matching lines count: {len(matching_lines)}")
                            print("    -> Sample matching lines:")
                            for ml in matching_lines[:30]:
                                print(f"      {ml}")
                except Exception as e:
                    print(f"    Could not read {lf}: {e}")
        else:
            print(f"Directory does not exist: {path}")

if __name__ == "__main__":
    main()
