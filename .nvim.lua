-- Project-local nvim config (needs `vim.o.exrc = true`; nvim asks you to :trust it once).
-- <leader>r  send the SQL paragraph under the cursor (or the visual selection) to the duckdb pane.
local target = vim.env.TALK_SQL_PANE or "apitalk:lake.0"

local function send(text)
  vim.fn.system({ "tmux", "load-buffer", "-b", "talksql", "-" }, text)
  vim.fn.system({ "tmux", "paste-buffer", "-d", "-b", "talksql", "-t", target })
  vim.fn.system({ "tmux", "send-keys", "-t", target, "Enter" })
end

vim.keymap.set("n", "<leader>r", function()
  vim.cmd('silent normal! "zyip')
  send(vim.fn.getreg("z"))
end, { desc = "talk: run SQL paragraph in duckdb pane" })

vim.keymap.set("x", "<leader>r", function()
  vim.cmd('silent normal! "zy')
  send(vim.fn.getreg("z"))
end, { desc = "talk: run selection in duckdb pane" })
