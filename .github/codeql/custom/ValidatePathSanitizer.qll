import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.security.dataflow.PathInjectionCustomizations

private class ValidatePathCallSanitizer extends PathInjection::Sanitizer {
  ValidatePathCallSanitizer() {
    exists(DataFlow::CallCfgNode call |
      call.getFunction().asExpr().(Name).getId() = "validate_path" and
      call.getLocation().getFile().getRelativePath() = "src/shrinkray/app.py"
    )
  }
}
