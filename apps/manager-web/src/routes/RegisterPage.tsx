import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Mail } from "lucide-react";
import { authApi, type IntegrationPolicy, type RegisterIntegration } from "../api";
import { parseList } from "./adminKeyForm";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Field, Notice } from "../components/kit";
import {
  AuthShell, PASSWORD_RULE, PasswordField, Spinner, TextLink, formError, passwordLengthError, MIN_PASSWORD_CHARS,
  type FriendlyError,
} from "../components/authKit";

type Step = 1 | 2 | 3;

const AVAILABLE = "inline-flex items-center gap-1 text-success";

export default function RegisterPage() {
  const [step, setStep] = useState<Step>(1);

  // Bước 1
  const [inviteKey, setInviteKey] = useState("");
  const [keyError, setKeyError] = useState<string | null>(null);
  const [keyLoading, setKeyLoading] = useState(false);
  // Chính sách tích hợp của mã mời (nếu mã cho phép tạo công cụ đẩy dữ liệu cùng lúc)
  const [policy, setPolicy] = useState<IntegrationPolicy | null>(null);
  const [withTool, setWithTool] = useState(false);
  const [toolName, setToolName] = useState("");
  const [toolKind, setToolKind] = useState<RegisterIntegration["client_kind"]>("primary_tool");
  const [toolPlatforms, setToolPlatforms] = useState<string[]>([]);
  const [toolPlatformsText, setToolPlatformsText] = useState("");
  const [provisioning, setProvisioning] = useState("");

  // Bước 2
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [regLoading, setRegLoading] = useState(false);
  const [regError, setRegError] = useState<FriendlyError | null>(null);

  // Validation debounce
  const [usernameOk, setUsernameOk] = useState<boolean | null>(null);
  const [emailOk, setEmailOk] = useState<boolean | null>(null);
  const usernameTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const emailTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Bước 3
  const [registeredEmail, setRegisteredEmail] = useState("");

  // ── Bước 1: kiểm tra mã mời ─────────────────────────────────────────────

  const validateKey = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!inviteKey.trim()) {
      setKeyError("Nhập mã mời bạn nhận được từ quản trị viên.");
      return;
    }
    setKeyLoading(true);
    setKeyError(null);
    try {
      const d = await authApi.validateKey(inviteKey.trim());
      if (d.valid) {
        const pol = d.integration_policy ?? (d.allow_integration_registration ? { allow_integration_registration: true } : null);
        setPolicy(pol && pol.allow_integration_registration ? pol : null);
        setWithTool(false);
        setStep(2);
      } else {
        setKeyError(`${d.reason || "Mã mời không hợp lệ"}. Kiểm tra lại mã, hoặc xin quản trị viên một mã mới.`);
      }
    } catch (err) {
      const fe = formError(err, "Chưa kiểm tra được mã mời");
      setKeyError(`${fe.title}. ${fe.body}`);
    } finally {
      setKeyLoading(false);
    }
  };

  // ── Bước 2: debounce kiểm tra tên đăng nhập / email ─────────────────────

  const checkUsername = useCallback((v: string) => {
    clearTimeout(usernameTimer.current);
    if (v.length < 3) { setUsernameOk(null); return; }
    usernameTimer.current = setTimeout(async () => {
      try {
        const d = await authApi.checkUsername(v);
        setUsernameOk(d.available);
      } catch { setUsernameOk(null); }
    }, 500);
  }, []);

  const checkEmail = useCallback((v: string) => {
    clearTimeout(emailTimer.current);
    if (!v.includes("@")) { setEmailOk(null); return; }
    emailTimer.current = setTimeout(async () => {
      try {
        const d = await authApi.checkEmail(v);
        setEmailOk(d.available);
      } catch { setEmailOk(null); }
    }, 500);
  }, []);

  useEffect(() => { checkUsername(username); }, [username, checkUsername]);
  useEffect(() => { checkEmail(email); }, [email, checkEmail]);

  const lengthError = passwordLengthError(password);
  const pwOk = password.length >= MIN_PASSWORD_CHARS && !lengthError;
  const confirmOk = confirmPw === password && confirmPw.length > 0;
  const policyPlatforms = policy?.allowed_platforms ?? [];
  const chosenPlatforms = policyPlatforms.length ? toolPlatforms : parseList(toolPlatformsText);
  const toolOk = !withTool || (toolName.trim().length > 0 && chosenPlatforms.length > 0);
  const canRegister = username.length >= 3 && usernameOk && email.includes("@") && emailOk && pwOk && confirmOk && toolOk;

  // Cho người dùng biết còn thiếu gì khi nút chưa bấm được (không chỉ làm mờ nút).
  const missing: string[] = [];
  if (username.length < 3) missing.push("tên đăng nhập");
  else if (usernameOk === false) missing.push("tên đăng nhập khác (tên này đã có người dùng)");
  if (!email.includes("@")) missing.push("email");
  else if (emailOk === false) missing.push("email khác (email này đã đăng ký)");
  if (!pwOk) missing.push("mật khẩu hợp lệ");
  if (!confirmOk) missing.push("nhập lại mật khẩu cho khớp");
  if (withTool && !toolOk) missing.push("tên công cụ và ít nhất một nền tảng");

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canRegister) return;
    setRegLoading(true);
    setRegError(null);
    try {
      const integration: RegisterIntegration | undefined = policy && withTool
        ? { create: true, tool_name: toolName.trim(), client_kind: toolKind, platforms: chosenPlatforms }
        : undefined;
      const d = await authApi.register(inviteKey.trim(), username.trim(), password, email.trim(), integration);
      setRegisteredEmail(d.email);
      setProvisioning(d.provisioning ?? "");
      setStep(3);
    } catch (err) {
      setRegError(formError(err, "Chưa đăng ký được"));
    } finally {
      setRegLoading(false);
    }
  };

  // ── Bước 3: gửi lại thư xác thực ────────────────────────────────────────

  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendNote, setResendNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const resendTimer = useRef<ReturnType<typeof setInterval>>(undefined);
  useEffect(() => () => clearInterval(resendTimer.current), []);

  const handleResend = async () => {
    if (resendCooldown > 0) return;
    setResendNote(null);
    try {
      await authApi.resendVerify(registeredEmail);
      setResendNote({ tone: "success", text: "Đã gửi lại thư xác thực. Thư có thể mất vài phút để tới." });
      setResendCooldown(60);
      resendTimer.current = setInterval(() => {
        setResendCooldown((s) => {
          if (s <= 1) { clearInterval(resendTimer.current); return 0; }
          return s - 1;
        });
      }, 1000);
    } catch (err) {
      const fe = formError(err, "Chưa gửi lại được");
      setResendNote({ tone: "error", text: `${fe.title}. ${fe.body}` });
    }
  };

  // ── Render ──────────────────────────────────────────────────────────────

  if (step === 3) {
    return (
      <AuthShell
        title="Kiểm tra email"
        description="Còn một bước nữa: xác thực email để kích hoạt tài khoản."
        footer={<p>Đã xác thực xong? <TextLink to="/login">Đăng nhập</TextLink></p>}
      >
        <div className="flex flex-col gap-5">
          <p className="flex items-start gap-3">
            <Mail className="mt-0.5 size-4 shrink-0 text-primary-text" aria-hidden />
            <span>
              Chúng tôi đã gửi thư xác thực tới <strong className="break-all">{registeredEmail}</strong>.
            </span>
          </p>
          <ol className="list-decimal space-y-1.5 pl-6 text-[0.9375rem]">
            <li>Mở hộp thư của bạn. Nếu chưa thấy thư sau vài phút, xem cả mục thư rác.</li>
            <li>Bấm vào liên kết xác thực trong thư.</li>
            <li>Quay lại đây và đăng nhập.</li>
          </ol>
          {provisioning === "pending" && (
            <p className="text-sm text-muted-foreground" data-testid="provisioning-pending">
              Công cụ đẩy dữ liệu bạn chọn tạo cùng lúc sẽ được khởi tạo sau khi email được xác thực.
            </p>
          )}
          {resendNote && (
            <Notice tone={resendNote.tone}>{resendNote.text}</Notice>
          )}
          <div className="flex flex-col gap-1">
            <Button variant="outline" size="lg" onClick={handleResend} disabled={resendCooldown > 0} className="w-full">
              {resendCooldown > 0 ? `Gửi lại thư sau ${resendCooldown} giây` : "Gửi lại thư xác thực"}
            </Button>
            <p className="text-sm text-muted-foreground">Sai địa chỉ email? Đăng ký lại với địa chỉ đúng.</p>
          </div>
        </div>
      </AuthShell>
    );
  }

  if (step === 1) {
    return (
      <AuthShell
        title="Đăng ký tài khoản"
        description="Bạn cần mã mời từ quản trị viên để tạo tài khoản. Bước 1 trên 2."
        footer={<p>Đã có tài khoản? <TextLink to="/login">Đăng nhập</TextLink></p>}
      >
        <form onSubmit={validateKey} noValidate className="flex flex-col gap-5">
          <Field label="Mã mời" hint="Dán mã mời bạn nhận được từ quản trị viên." error={keyError}>
            {(p) => (
              <Input
                {...p}
                name="invite"
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
                value={inviteKey}
                onChange={(e) => { setInviteKey(e.target.value); setKeyError(null); }}
                autoFocus
              />
            )}
          </Field>
          <Button type="submit" size="lg" disabled={keyLoading} className="w-full">
            {keyLoading && <Spinner />}
            Tiếp tục
          </Button>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Tạo tài khoản"
      description="Điền thông tin của bạn. Bước 2 trên 2."
      footer={<p>Đã có tài khoản? <TextLink to="/login">Đăng nhập</TextLink></p>}
    >
      <form onSubmit={handleRegister} noValidate className="flex flex-col gap-5">
        {regError && <Notice tone="error" title={regError.title}>{regError.body}</Notice>}

        <Field
          label="Tên đăng nhập"
          hint={usernameOk
            ? <span className={AVAILABLE}><Check className="size-4" aria-hidden />Tên này dùng được</span>
            : "3 đến 30 ký tự: chữ không dấu, số và gạch dưới."}
          error={usernameOk === false ? "Tên này đã có người dùng. Hãy chọn tên khác." : null}
        >
          {(p) => (
            <Input
              {...p}
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(e) => setUsername(e.target.value.replace(/[^a-zA-Z0-9_]/g, ""))}
              maxLength={30}
              autoFocus
            />
          )}
        </Field>

        <Field
          label="Email"
          hint={emailOk
            ? <span className={AVAILABLE}><Check className="size-4" aria-hidden />Email này dùng được</span>
            : "Chúng tôi gửi thư xác thực tới email này. Dùng email bạn đọc được."}
          error={emailOk === false ? "Email này đã được đăng ký. Hãy đăng nhập, hoặc dùng “Quên mật khẩu” nếu bạn quên mật khẩu." : null}
        >
          {(p) => (
            <Input
              {...p}
              name="email"
              type="email"
              autoComplete="email"
              autoCapitalize="none"
              spellCheck={false}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          )}
        </Field>

        <PasswordField
          label="Mật khẩu"
          name="new-password"
          autoComplete="new-password"
          value={password}
          onChange={setPassword}
          error={lengthError}
          hint={
            <>
              {PASSWORD_RULE}
              {password.length > 0 && password.length < MIN_PASSWORD_CHARS && (
                <span className="mt-0.5 block">Cần thêm {MIN_PASSWORD_CHARS - password.length} ký tự.</span>
              )}
            </>
          }
        />

        <Field label="Nhập lại mật khẩu" error={confirmPw && !confirmOk ? "Hai mật khẩu chưa khớp. Nhập lại cho giống ô trên." : null}>
          {(p) => (
            <Input
              {...p}
              name="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
            />
          )}
        </Field>

        {/* Tạo công cụ đẩy dữ liệu cùng lúc: chỉ hiện khi mã mời cho phép (integration_policy) */}
        {policy && (
          <div className="border-t border-border pt-4" data-testid="register-tool">
            <label className="flex min-h-11 cursor-pointer items-center gap-3 font-semibold">
              <input type="checkbox" className="size-4 accent-primary" checked={withTool}
                     aria-describedby="register-tool-hint"
                     onChange={(e) => setWithTool(e.target.checked)} />
              Tạo công cụ đẩy dữ liệu
            </label>
            <p id="register-tool-hint" className="ml-8 text-sm text-muted-foreground">
              Không bắt buộc. Chọn nếu bạn muốn có sẵn một công cụ để đẩy dữ liệu kênh lên Manager.
            </p>
            {withTool && (
              <div className="mt-4 flex flex-col gap-4">
                <Field label="Tên công cụ" hint="Đặt tên dễ nhớ, ví dụ: Công cụ đăng bài.">
                  {(p) => <Input {...p} value={toolName} maxLength={100} onChange={(e) => setToolName(e.target.value)} />}
                </Field>
                <Field label="Loại công cụ">
                  {(p) => (
                    <select {...p} value={toolKind}
                            onChange={(e) => setToolKind(e.target.value as RegisterIntegration["client_kind"])}
                            className="h-11 w-full rounded-md border border-border-strong bg-surface-1 px-3 text-base">
                      <option value="primary_tool">Công cụ chính</option>
                      <option value="fallback_tool">Công cụ dự phòng</option>
                      <option value="service">Dịch vụ tự động</option>
                    </select>
                  )}
                </Field>
                <fieldset className="flex flex-col gap-1">
                  <legend className="mb-1 text-sm font-semibold">Nền tảng</legend>
                  {policyPlatforms.length ? (
                    <div className="flex flex-wrap gap-x-5">
                      {policyPlatforms.map((pl) => (
                        <label key={pl} className="flex min-h-11 cursor-pointer items-center gap-2">
                          <input type="checkbox" className="size-4 accent-primary" checked={toolPlatforms.includes(pl)}
                                 onChange={(e) => setToolPlatforms((cur) => e.target.checked ? [...cur, pl] : cur.filter((x) => x !== pl))} />
                          {pl}
                        </label>
                      ))}
                    </div>
                  ) : (
                    <Input aria-label="Nền tảng" value={toolPlatformsText} placeholder="facebook, youtube"
                           onChange={(e) => setToolPlatformsText(e.target.value)} />
                  )}
                </fieldset>
                <p className="text-sm text-muted-foreground">
                  Công cụ được khởi tạo sau khi bạn xác thực email. Khóa kết nối cấp ở trang &ldquo;Công cụ đẩy dữ liệu&rdquo;.
                </p>
              </div>
            )}
          </div>
        )}

        <div className="flex flex-col gap-3">
          {!canRegister && missing.length > 0 && (
            <p className="text-sm text-muted-foreground" aria-live="polite">
              Để đăng ký, còn cần: {missing.join("; ")}.
            </p>
          )}
          <Button type="submit" size="lg" disabled={regLoading || !canRegister} className="w-full">
            {regLoading && <Spinner />}
            Đăng ký
          </Button>
          <Button type="button" variant="ghost" onClick={() => setStep(1)} className="w-full">
            Quay lại bước nhập mã mời
          </Button>
        </div>
      </form>
    </AuthShell>
  );
}
