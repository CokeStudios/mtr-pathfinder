## Install requirements 安装依赖项
For Python 3.7 - 3.12 only.

需 Python 3.7 - 3.12。

```
pip3 install -U fontTools opencc pillow networkx requests
```

If there's an error, please try installing older versions of OpenCC.

如报错，请尝试安装旧版 OpenCC。

## Usage 使用
Download the repo zip.

For MTR 3.x.x / 4.0.0 pathfinding (*Pathfinder v3.0.0*), edit params in ```run()``` in ```mtr_pathfinder.py``` and run ```mtr_pathfinder.py```.

For MTR 4.0.0 **real-time pathfinding** (*Pathfinder v4.0.0*), edit params in ```run()``` in ```mtr_pathfinder_v4.py``` and run ```mtr_pathfinder_v4.py```.

You can also call ```main()``` in other files.

下载仓库 zip。

如果使用 MTR 3.x.x / 4.0.0（*寻路程序 v3.0.0*），编辑 ```mtr_pathfinder.py``` 中的 ```run()``` 以更改参数，运行 ```mtr_pathfinder.py``` 即可寻路。

如果使用 MTR 4.0.0 **实时寻路**（*寻路程序 v4.0.0*），编辑 ```mtr_pathfinder_v4.py``` 中的 ```run()``` 以更改参数，运行 ```mtr_pathfinder_v4.py``` 即可寻路。

也可以在其他文件中调用 ```main()```。
