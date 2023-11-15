import shutil
if __name__ == "__main__":
  print("Zipping data folder...")
  shutil.make_archive("data", 'zip', "data")
  print("Zipping completed!")