# Benchmarks

The production native benchmark runner is:

```powershell
python core\native\tests\benchmark_native.py --profile small --destination-root D:\qfc-bench --workers 1,4,8,16
```

`legacy_python/` contains the data generator, runner, and reports created for the former
FastestCopy Python prototype. They are retained as historical measurements and should not be
presented as results from the current QuickFileCopy native engine.
