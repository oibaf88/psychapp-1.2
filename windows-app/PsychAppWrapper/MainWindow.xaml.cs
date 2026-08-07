using System;
using Microsoft.UI.Xaml;

namespace PsychAppWrapper;

public sealed partial class MainWindow : Window
{
    // Same server the PWA install flow in package-mobile.ps1 points at.
    // Override with the PSYCHAPP_URL environment variable to target a LAN host.
    private static readonly string ServerUrl =
        Environment.GetEnvironmentVariable("PSYCHAPP_URL") ?? "http://localhost:5173";

    public MainWindow()
    {
        InitializeComponent();
        Title = "PsychApp";
        Loaded += async (_, _) =>
        {
            await Browser.EnsureCoreWebView2Async();
            Browser.CoreWebView2.Navigate(ServerUrl);
        };
    }
}
